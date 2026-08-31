"""有货未展示看板 —— 纯本地桌面软件（Tkinter，不走浏览器）。

表格每行内嵌产品缩略图；支持搜索、筛选、排序、按系列分组。

运行：
    python panel_app.py          # 或双击 start_panel.bat
"""

import io
import sys
import threading
import urllib.request
from pathlib import Path

import panel_data

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:
    tk = None
    ttk = None

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

APP_VERSION = "1.3.1"
ROW_HEIGHT = 58
THUMB = (52, 52)
IMAGE_BATCH = 40
PLACEHOLDER_COLOR = "#d1d5db"
SCROLL_UNITS = 8

C_HEADER = "#1e4f8a"
C_BG = "#f0f4f8"
C_CARD_GAP = "#dc2626"
C_CARD_GAP_BG = "#fee2e2"
C_CARD_EXEMPT = "#ca8a04"
C_CARD_EXEMPT_BG = "#fef9c3"
C_CARD_OK = "#16a34a"
C_CARD_OK_BG = "#dcfce7"
C_CARD_INFO = "#2563eb"
C_CARD_INFO_BG = "#dbeafe"
C_CARD_NEUTRAL = "#64748b"
C_CARD_NEUTRAL_BG = "#f1f5f9"
C_ROW_GAP = "#fff1f2"
C_ROW_EXEMPT = "#fffbeb"
C_ROW_OK = "#f0fdf4"
C_ROW_ALT = "#fafbfc"
C_ROW_DISC = "#f3f4f6"
C_TEXT = "#1e293b"
C_MUTED = "#64748b"

SORTABLE_COLS = {
    "code": lambda p: (p.get("code") or "").lower(),
    "name": lambda p: (p.get("name") or "").lower(),
    "family": lambda p: (p.get("family") or "").lower(),
    "price": lambda p: p.get("price") if p.get("price") is not None else -1,
    "stock": lambda p: float(p.get("stock_qty") or 0),
    "display": lambda p: 0 if p.get("displayed") else 1,
    "discontinue": lambda p: 0 if p.get("discontinued") else 1,
    "status": lambda p: (
        0 if p.get("gap") else 1 if p.get("exempted") else 2 if p.get("in_stock") else 3
    ),
}


class PanelApp:
    def __init__(self):
        if tk is None:
            raise RuntimeError("当前 Python 缺少 Tkinter，无法启动桌面界面。")
        self.root = tk.Tk()
        self.root.title("有货未展示看板")
        self.root.geometry("1360x860")
        self.root.minsize(1080, 700)
        self.root.configure(bg=C_BG)

        self._img_cache = {}
        self._pending_images = set()
        self._reload_token = 0
        self._render_token = 0
        self._image_semaphore = threading.Semaphore(6)
        self._products_by_iid = {}
        self._iid_to_url = {}
        self._scroll_after_id = None
        self._filter_after_id = None
        self._cached_products = []
        self._cached_summary = {}
        self._sort_col = None
        self._sort_reverse = False

        self._tree = None
        self._tree_vscroll = None
        self._prefix_tree = None
        self._prefix_vscroll = None
        self._notebook = None
        self._tab_products = None
        self._placeholder_photo = None
        self._stat_labels = {}
        self._stat_cards = {}
        self._quick_filter = None

        regions = panel_data.list_regions()
        if not regions:
            raise RuntimeError("region_runner_config.json 中未配置任何地区。")
        default_region = panel_data.default_region()
        stores = panel_data.list_stores(default_region)

        self.store_var = tk.StringVar(value=panel_data.ALL_STORES)
        self.only_gap_var = tk.BooleanVar(value=False)
        self.only_exempted_var = tk.BooleanVar(value=False)
        self.source_var = tk.StringVar(value="")
        self.search_var = tk.StringVar()
        self.stock_filter_var = tk.StringVar(value="全部")
        self.display_filter_var = tk.StringVar(value="全部")
        self.discontinue_filter_var = tk.StringVar(value="在产")
        self.group_sort_var = tk.StringVar(value="字母序")
        self.result_count_var = tk.StringVar(value="")

        self._region_labels = {r["key"]: r["label"] for r in regions}
        self._setup_styles()
        self._build_ui(stores, regions)
        self.search_var.trace_add("write", lambda *_: self._debounce_refresh())
        self.reload()

    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", rowheight=ROW_HEIGHT, font=("Segoe UI", 10),
                        background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"),
                        background="#e2e8f0", foreground=C_TEXT)
        style.map("Treeview", background=[("selected", "#bfdbfe")])
        style.configure("TCombobox", padding=4)
        style.configure("Tool.TButton", padding=(10, 4))
        style.configure("Vertical.TScrollbar", width=18, arrowsize=14)
        style.configure("Prefix.Treeview", rowheight=34, font=("Segoe UI", 10))

    def _build_ui(self, stores, regions):
        header = tk.Frame(self.root, bg=C_HEADER, padx=16, pady=10)
        header.pack(fill=tk.X)
        tk.Label(header, text="有货未展示看板", bg=C_HEADER, fg="white",
                 font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT)
        tk.Label(header, text=f"v{APP_VERSION}", bg=C_HEADER, fg="#93c5fd",
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(8, 0), pady=(6, 0))
        tk.Label(header, textvariable=self.source_var, bg=C_HEADER, fg="#cbd5e1",
                 font=("Segoe UI", 9)).pack(side=tk.RIGHT)

        toolbar = tk.Frame(self.root, bg="white", padx=14, pady=10)
        toolbar.pack(fill=tk.X, padx=12, pady=(10, 0))

        tk.Label(toolbar, text="地区", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        region_values = []
        default_index = 0
        for i, r in enumerate(regions):
            label = r["label"]
            if not r.get("has_latest"):
                label += " (尚无数据)"
            region_values.append(f"{r['key']} {label}")
            if r["key"] == panel_data.default_region():
                default_index = i
        self.region_combo = ttk.Combobox(toolbar, width=16, state="readonly", values=region_values)
        self.region_combo.current(default_index)
        self.region_combo.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(2, 0))
        self.region_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_region_change())

        tk.Label(toolbar, text="店面", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).grid(row=0, column=1, sticky="w")
        self.store_combo = ttk.Combobox(toolbar, width=20, state="readonly",
                                         values=stores, textvariable=self.store_var, height=18)
        self.store_combo.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(2, 0))
        self.store_combo.bind("<<ComboboxSelected>>", lambda _e: self.reload())

        self.reload_btn = ttk.Button(toolbar, text="刷新数据", style="Tool.TButton",
                                     command=lambda: self.reload(force=True))
        self.reload_btn.grid(row=1, column=2, sticky="w", pady=(2, 0))

        filter_bar = tk.Frame(self.root, bg="white", padx=14, pady=8)
        filter_bar.pack(fill=tk.X, padx=12, pady=(6, 0))

        tk.Label(filter_bar, text="搜索", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        search_entry = ttk.Entry(filter_bar, textvariable=self.search_var, width=28)
        search_entry.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(2, 0))

        filters = [
            ("库存", self.stock_filter_var, ("全部", "有货", "无货"), 1),
            ("展示", self.display_filter_var, ("全部", "已展示", "未展示"), 2),
            ("停产", self.discontinue_filter_var, ("在产", "全部", "已停产"), 3),
            ("组排序", self.group_sort_var, ("字母序", "数量多到少"), 4),
        ]
        for label, var, values, col in filters:
            tk.Label(filter_bar, text=label, bg="white", fg=C_MUTED, font=("Segoe UI", 9)).grid(
                row=0, column=col, sticky="w")
            cb = ttk.Combobox(filter_bar, width=10, state="readonly", textvariable=var, values=values)
            cb.grid(row=1, column=col, sticky="w", padx=(0, 10), pady=(2, 0))
            cb.bind("<<ComboboxSelected>>", lambda _e: self._refresh_view())

        ttk.Checkbutton(filter_bar, text="只看有货未展示", variable=self.only_gap_var,
                        command=self._on_only_gap_toggle).grid(row=1, column=5, sticky="w", padx=(4, 0))
        ttk.Checkbutton(filter_bar, text="只看同组豁免", variable=self.only_exempted_var,
                        command=self._on_only_exempted_toggle).grid(row=1, column=6, sticky="w", padx=(8, 0))

        tk.Label(filter_bar, textvariable=self.result_count_var, bg="white", fg=C_MUTED,
                 font=("Segoe UI", 9)).grid(row=1, column=7, sticky="e", padx=(12, 0))
        filter_bar.columnconfigure(7, weight=1)

        cards = tk.Frame(self.root, bg=C_BG, padx=12, pady=8)
        cards.pack(fill=tk.X)
        card_defs = [
            ("gap", "有货未展示", "0", C_CARD_GAP_BG, C_CARD_GAP),
            ("exempted", "同组豁免", "0", C_CARD_EXEMPT_BG, C_CARD_EXEMPT),
            ("in_stock", "有货产品", "0", C_CARD_OK_BG, C_CARD_OK),
            ("rate", "有货率", "-", C_CARD_INFO_BG, C_CARD_INFO),
            ("total", "纳入分析", "0", C_CARD_NEUTRAL_BG, C_CARD_NEUTRAL),
        ]
        for i, (key, title, val, bg, fg) in enumerate(card_defs):
            card = tk.Frame(cards, bg=bg, padx=16, pady=10, cursor="hand2",
                            highlightthickness=2, highlightbackground=bg)
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0 if i == 0 else 6, 0))
            title_lbl = tk.Label(card, text=title, bg=bg, fg=fg, font=("Segoe UI", 9), cursor="hand2")
            title_lbl.pack(anchor="w")
            val_lbl = tk.Label(card, text=val, bg=bg, fg=fg, font=("Segoe UI", 18, "bold"), cursor="hand2")
            val_lbl.pack(anchor="w", pady=(2, 0))
            if key in ("gap", "exempted", "in_stock"):
                hint = tk.Label(card, text="点击筛选", bg=bg, fg=fg, font=("Segoe UI", 8), cursor="hand2")
                hint.pack(anchor="w")
                for w in (card, title_lbl, val_lbl, hint):
                    w.bind("<Button-1>", lambda _e, k=key: self._on_stat_card_click(k))
            self._stat_labels[key] = val_lbl
            self._stat_cards[key] = card

        self._stock_source_lbl = tk.Label(cards, text="", bg=C_BG, fg=C_MUTED, font=("Segoe UI", 9))
        self._stock_source_lbl.pack(side=tk.RIGHT, padx=8)

        table_wrap = tk.Frame(self.root, bg="white")
        table_wrap.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self._notebook = ttk.Notebook(table_wrap)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._tab_products = ttk.Frame(self._notebook)
        tab_prefix = ttk.Frame(self._notebook)
        self._notebook.add(self._tab_products, text="产品明细")
        self._notebook.add(tab_prefix, text="SKU前三位汇总")

        inner = tk.Frame(self._tab_products, bg="white")
        inner.pack(fill=tk.BOTH, expand=True)

        columns = ("code", "name", "family", "price", "stock", "display", "discontinue", "status")
        self._tree = ttk.Treeview(inner, columns=columns, show="tree headings", selectmode="browse")
        self._tree.heading("#0", text="产品图")
        self._tree.column("#0", width=68, stretch=False, anchor="center")
        headings = {
            "code": ("编码", 96), "name": ("名称", 280), "family": ("系列", 96),
            "price": ("价格", 72), "stock": ("库存", 80), "display": ("展示", 64),
            "discontinue": ("停产", 56), "status": ("状态", 116),
        }
        for col, (text, width) in headings.items():
            self._tree.heading(col, text=text, command=lambda c=col: self._on_sort_column(c))
            anchor = "w" if col in ("code", "name", "family") else "center"
            self._tree.column(col, width=width, anchor=anchor, stretch=(col == "name"))

        for tag, bg in (("gap", C_ROW_GAP), ("exempted", C_ROW_EXEMPT), ("ok", C_ROW_OK),
                        ("alt", C_ROW_ALT), ("discontinued", C_ROW_DISC), ("group", "#e2e8f0")):
            self._tree.tag_configure(tag, background=bg)

        self._tree_vscroll = ttk.Scrollbar(inner, orient="vertical", command=self._on_tree_yscroll)
        self._tree.configure(yscrollcommand=self._tree_vscroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._tree_vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        # ── SKU 前三位汇总表 ──
        prefix_inner = tk.Frame(tab_prefix, bg="white")
        prefix_inner.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            prefix_inner,
            text="按 SKU 编码前三位汇总（在产 SKU 数量）· 双击某行可筛选该前缀 · 有货未展示/豁免需先选店面",
            bg="white", fg=C_MUTED, font=("Segoe UI", 9),
        ).pack(anchor="w", padx=4, pady=(0, 6))

        pcols = ("prefix", "total", "in_stock", "in_stock_rate", "displayed",
                 "display_rate", "gap", "exempted")
        self._prefix_tree = ttk.Treeview(
            prefix_inner, columns=pcols, show="headings",
            selectmode="browse", style="Prefix.Treeview",
        )
        pheads = {
            "prefix": ("SKU前缀", 72), "total": ("在产SKU数", 80), "in_stock": ("有货数", 64),
            "in_stock_rate": ("有货率", 72), "displayed": ("有货已展示", 88),
            "display_rate": ("展示覆盖率", 88), "gap": ("有货未展示", 88), "exempted": ("同组豁免", 72),
        }
        for col, (text, width) in pheads.items():
            self._prefix_tree.heading(col, text=text)
            self._prefix_tree.column(col, width=width, anchor="center" if col != "prefix" else "w")
        self._prefix_tree.tag_configure("gap", background=C_ROW_GAP)
        self._prefix_tree.tag_configure("warn", background="#fff7ed")
        self._prefix_tree.tag_configure("ok", background=C_ROW_OK)
        self._prefix_tree.tag_configure("alt", background=C_ROW_ALT)

        self._prefix_vscroll = ttk.Scrollbar(prefix_inner, orient="vertical", command=self._on_prefix_yscroll)
        self._prefix_tree.configure(yscrollcommand=self._prefix_vscroll.set)
        self._prefix_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._prefix_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._prefix_tree.bind("<Double-1>", self._on_prefix_double_click)

        self._placeholder_photo = self._make_placeholder_photo()
        for widget in (inner, self._tab_products, self._tree):
            widget.bind("<MouseWheel>", self._on_product_wheel)
            widget.bind("<Button-4>", lambda _e: self._scroll_tree(-1))
            widget.bind("<Button-5>", lambda _e: self._scroll_tree(1))
        for widget in (prefix_inner, tab_prefix, self._prefix_tree):
            widget.bind("<MouseWheel>", self._on_prefix_wheel)
            widget.bind("<Button-4>", lambda _e: self._scroll_prefix(-1))
            widget.bind("<Button-5>", lambda _e: self._scroll_prefix(1))

    def _make_placeholder_photo(self):
        if Image is not None and ImageTk is not None:
            return ImageTk.PhotoImage(Image.new("RGB", THUMB, PLACEHOLDER_COLOR))
        img = tk.PhotoImage(width=THUMB[0], height=THUMB[1])
        img.put(PLACEHOLDER_COLOR, to=(0, 0, THUMB[0], THUMB[1]))
        return img

    def _on_tree_yscroll(self, *args):
        self._tree.yview(*args)
        self._debounce_visible_images()

    def _on_prefix_yscroll(self, *args):
        if self._prefix_tree:
            self._prefix_tree.yview(*args)

    def _debounce_visible_images(self):
        if self._scroll_after_id:
            self.root.after_cancel(self._scroll_after_id)
        self._scroll_after_id = self.root.after(80, self._load_visible_images)

    def _debounce_refresh(self):
        if self._filter_after_id:
            self.root.after_cancel(self._filter_after_id)
        self._filter_after_id = self.root.after(250, self._refresh_view)

    def _scroll_tree(self, direction):
        if self._tree:
            self._tree.yview_scroll(direction * SCROLL_UNITS, "units")
            self._debounce_visible_images()

    def _scroll_prefix(self, direction):
        if self._prefix_tree:
            self._prefix_tree.yview_scroll(direction * SCROLL_UNITS, "units")

    def _on_product_wheel(self, event):
        if not self._tree:
            return
        step = -1 if (hasattr(event, "delta") and event.delta > 0) else 1
        self._scroll_tree(step)

    def _on_prefix_wheel(self, event):
        step = -1 if (hasattr(event, "delta") and event.delta > 0) else 1
        self._scroll_prefix(step)

    def _visible_iids(self):
        if not self._tree:
            return []
        height = max(self._tree.winfo_height(), ROW_HEIGHT)
        seen, y = [], 0
        while y < height + ROW_HEIGHT * 4:
            iid = self._tree.identify_row(y)
            if not iid or iid in seen or iid not in self._iid_to_url:
                y += ROW_HEIGHT
                continue
            seen.append(iid)
            y += ROW_HEIGHT
        return seen

    def _is_store_selected(self):
        return self.store_var.get() != panel_data.ALL_STORES

    def _on_only_gap_toggle(self):
        if self.only_gap_var.get():
            self.only_exempted_var.set(False)
            self._quick_filter = "gap"
        elif self._quick_filter == "gap":
            self._quick_filter = None
        self._refresh_view()

    def _on_only_exempted_toggle(self):
        if self.only_exempted_var.get():
            self.only_gap_var.set(False)
            self._quick_filter = "exempted"
        elif self._quick_filter == "exempted":
            self._quick_filter = None
        self._refresh_view()

    def _on_stat_card_click(self, key):
        if key in ("gap", "exempted") and not self._is_store_selected():
            return
        if key == "gap":
            self.only_exempted_var.set(False)
            self.only_gap_var.set(not self.only_gap_var.get())
            self._quick_filter = "gap" if self.only_gap_var.get() else None
        elif key == "exempted":
            self.only_gap_var.set(False)
            self.only_exempted_var.set(not self.only_exempted_var.get())
            self._quick_filter = "exempted" if self.only_exempted_var.get() else None
        elif key == "in_stock":
            self.stock_filter_var.set("无货" if self.stock_filter_var.get() == "有货" else "有货")
        self._refresh_view()

    def _update_stat_card_highlight(self):
        highlights = {
            "gap": self.only_gap_var.get(),
            "exempted": self.only_exempted_var.get(),
            "in_stock": self.stock_filter_var.get() == "有货",
        }
        for key, card in self._stat_cards.items():
            color = "#1e40af" if highlights.get(key) else card.cget("bg")
            card.configure(highlightbackground=color)

    def _current_region(self):
        text = self.region_combo.get().strip()
        return text.split(" ")[0] if text else panel_data.default_region()

    def _set_controls_state(self, enabled):
        state = "readonly" if enabled else "disabled"
        self.region_combo.configure(state=state)
        self.store_combo.configure(state=state)
        self.reload_btn.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_busy(self, busy):
        self.root.config(cursor="watch" if busy else "")

    def _run_bg(self, worker, on_done):
        def _thread():
            try:
                result = worker()
                err = None
            except Exception as exc:
                result, err = None, exc
            self.root.after(0, lambda: on_done(err, result))
        threading.Thread(target=_thread, daemon=True).start()

    def _on_region_change(self):
        region = self._current_region()
        panel_data.clear_region_cache(region)
        self._set_controls_state(False)
        self._set_busy(True)

        def done(err, stores):
            self._set_busy(False)
            self._set_controls_state(True)
            if err:
                return
            self.store_combo.configure(values=stores)
            if self.store_var.get() not in stores:
                self.store_var.set(stores[0] if stores else panel_data.ALL_STORES)
            self.reload()

        self._run_bg(lambda: panel_data.list_stores(region), done)

    def _pil_to_photo(self, im):
        if Image is None or ImageTk is None:
            return None
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            if im.mode == "P":
                im = im.convert("RGBA")
            alpha = im.split()[-1] if im.mode in ("RGBA", "LA") else None
            bg.paste(im, mask=alpha)
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail(THUMB)
        return ImageTk.PhotoImage(im)

    def _fetch_image_bytes(self, url):
        req = urllib.request.Request(
            panel_data.normalize_url(url),
            headers={"User-Agent": "Mozilla/5.0 WarehousePanel/1.2"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()

    def _schedule_row_image(self, iid, raw, render_token):
        if not raw or raw in self._pending_images:
            return
        cache_key = f"{raw}@{THUMB[0]}x{THUMB[1]}"
        if cache_key in self._img_cache:
            if self._tree.exists(iid):
                self._tree.item(iid, image=self._img_cache[cache_key])
            return
        self._pending_images.add(raw)

        def worker():
            photo = None
            try:
                with self._image_semaphore:
                    if str(raw).lower().startswith(("http://", "https://")):
                        data = self._fetch_image_bytes(raw)
                        if Image is not None:
                            photo = self._pil_to_photo(Image.open(io.BytesIO(data)))
                    elif Path(raw).exists() and Image is not None:
                        photo = self._pil_to_photo(Image.open(raw))
            except Exception:
                pass

            def apply():
                self._pending_images.discard(raw)
                if render_token != self._render_token:
                    return
                if photo:
                    self._img_cache[cache_key] = photo
                if self._tree.exists(iid):
                    self._tree.item(iid, image=photo or self._placeholder_photo)

            if self.root.winfo_exists():
                self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _load_visible_images(self):
        if not self._tree:
            return
        token = self._render_token
        for iid in self._visible_iids()[:IMAGE_BATCH]:
            raw = self._iid_to_url.get(iid)
            if raw:
                self._schedule_row_image(iid, raw, token)

    def reload(self, force=False):
        self._reload_token += 1
        token = self._reload_token
        region = self._current_region()
        store = self.store_var.get()
        self._set_controls_state(False)
        self._set_busy(True)

        def worker():
            return panel_data.build_products(
                store=store, only_gap=False, include_discontinued=True,
                region=region, force_refresh=force,
            )

        def done(err, data):
            if token != self._reload_token:
                return
            self._set_busy(False)
            self._set_controls_state(True)
            if err:
                self._stat_labels["gap"].configure(text="!")
                return
            self._cached_products = data["products"]
            self._cached_summary = data["summary"]
            src = self._region_labels.get(region, region)
            self.source_var.set(f"数据源：{src}  |  {Path(data['stock_path']).name}")
            self._refresh_view()

        self._run_bg(worker, done)

    def _apply_client_filters(self, products):
        q = self.search_var.get().strip().lower()
        stock_f = self.stock_filter_var.get()
        display_f = self.display_filter_var.get()
        disc_f = self.discontinue_filter_var.get()
        only_gap = self.only_gap_var.get()
        only_exempted = self.only_exempted_var.get()

        out = []
        for p in products:
            if q:
                hay = f"{p.get('code', '')} {p.get('name', '')} {p.get('family', '')}".lower()
                if q not in hay:
                    continue
            if stock_f == "有货" and not p.get("in_stock"):
                continue
            if stock_f == "无货" and p.get("in_stock"):
                continue
            if display_f == "已展示" and not p.get("displayed"):
                continue
            if display_f == "未展示" and p.get("displayed"):
                continue
            if disc_f == "在产" and p.get("discontinued"):
                continue
            if disc_f == "已停产" and not p.get("discontinued"):
                continue
            if only_gap and not p.get("gap"):
                continue
            if only_exempted and not p.get("exempted"):
                continue
            out.append(p)
        return out

    def _sort_products(self, items):
        if self._sort_col and self._sort_col in SORTABLE_COLS:
            key_fn = SORTABLE_COLS[self._sort_col]
            return sorted(items, key=key_fn, reverse=self._sort_reverse)
        return sorted(items, key=lambda p: (
            not p.get("gap"), not p.get("exempted"), not p.get("in_stock"),
            -float(p.get("stock_qty") or 0), p.get("code") or "",
        ))

    def _group_products(self, products):
        groups = {}
        for item in products:
            label = item.get("exemption_group_label") or item.get("family") or "未分类"
            groups.setdefault(label, []).append(item)

        result = []
        for label, items in groups.items():
            result.append((label, self._sort_products(items)))

        if self.group_sort_var.get() == "数量多到少":
            result.sort(key=lambda x: -len(x[1]))
        else:
            result.sort(key=lambda x: x[0].lower())
        return result

    def _on_sort_column(self, col):
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False
        self._refresh_view()

    def _refresh_view(self):
        if not self._cached_products:
            return
        s = self._cached_summary

        store_specific = s.get("store_specific", self._is_store_selected())

        def pct(v):
            return "-" if v is None else f"{v:.1f}%"

        if store_specific:
            self._stat_labels["gap"].configure(text=str(s.get("not_displayed_count", 0)), font=("Segoe UI", 18, "bold"))
            self._stat_labels["exempted"].configure(text=str(s.get("exempted_count", 0)), font=("Segoe UI", 18, "bold"))
        else:
            self._stat_labels["gap"].configure(text="请选择店面", font=("Segoe UI", 11, "bold"))
            self._stat_labels["exempted"].configure(text="请选择店面", font=("Segoe UI", 11, "bold"))
        self._stat_labels["in_stock"].configure(text=str(s.get("in_stock_count", 0)))
        self._stat_labels["rate"].configure(text=pct(s.get("in_stock_rate")))
        self._stat_labels["total"].configure(text=str(s.get("total_non_discontinue", 0)))
        self._stock_source_lbl.configure(
            text=f"店面：{s.get('store', '-')}  |  库存来源：{s.get('stock_sources', '-')}"
        )
        self._update_stat_card_highlight()

        filtered = self._apply_client_filters(self._cached_products)
        active_total = sum(1 for p in self._cached_products if not p.get("discontinued"))
        self.result_count_var.set(f"显示 {len(filtered)} / 在产 {active_total} 条")
        self._render_tree(filtered)
        self._render_prefix_table(store_specific)

    def _prefix_row_tag(self, row, index):
        if row.get("gap_count", 0) > 0:
            return ("gap",)
        rate = row.get("display_coverage_rate")
        if rate is not None and rate < 50 and row.get("in_stock_count", 0) > 0:
            return ("warn",)
        if rate is not None and rate >= 80:
            return ("ok",)
        if index % 2 == 1:
            return ("alt",)
        return ()

    def _render_prefix_table(self, store_specific=True):
        if not self._prefix_tree:
            return
        rows = panel_data.aggregate_by_sku_prefix(
            self._cached_products, store_specific=store_specific,
        )
        if self._prefix_tree.get_children():
            self._prefix_tree.delete(*self._prefix_tree.get_children())

        def pct(v):
            return "-" if v is None else f"{v:.1f}%"

        for idx, row in enumerate(rows):
            gap_val = row["gap_count"] if store_specific else "-"
            exempt_val = row["exempted_count"] if store_specific else "-"
            self._prefix_tree.insert(
                "", tk.END,
                values=(
                    row["prefix"],
                    row["total"],
                    row["in_stock_count"],
                    pct(row["in_stock_rate"]),
                    row["displayed_in_stock"],
                    pct(row["display_coverage_rate"]),
                    gap_val,
                    exempt_val,
                ),
                tags=self._prefix_row_tag(row, idx) if store_specific else ("alt",),
            )

    def _on_prefix_double_click(self, _event=None):
        sel = self._prefix_tree.selection() if self._prefix_tree else ()
        if not sel:
            return
        values = self._prefix_tree.item(sel[0], "values")
        if not values:
            return
        self.search_var.set(str(values[0]))
        if self._notebook:
            self._notebook.select(self._tab_products)
        self._refresh_view()

    def _row_tag(self, item, index):
        if item.get("discontinued"):
            return ("discontinued",)
        if item.get("gap"):
            return ("gap",)
        if item.get("exempted"):
            return ("exempted",)
        if item.get("in_stock") and item.get("displayed"):
            return ("ok",)
        if index % 2 == 1:
            return ("alt",)
        return ()

    def _tree_row_values(self, item):
        price = f"{item['price']:,.2f}" if item.get("price") is not None else "-"
        stock = int(item["stock_qty"]) if item.get("in_stock") else 0
        if item.get("stock_breakdown") and item.get("in_stock"):
            stock = f"{stock} ({item['stock_breakdown']})"
        displayed = "已展示" if item.get("displayed") else "未展示"
        discontinue = "是" if item.get("discontinued") else "否"
        if item.get("gap"):
            status = "★ 有货未展示"
        elif item.get("exempted"):
            status = "○ 同组已展示"
        elif item.get("discontinued"):
            status = "已停产"
        elif item.get("in_stock"):
            status = "有货"
        else:
            status = "无货"
        return (
            item["code"], item.get("name") or "", item.get("family") or "",
            price, stock, displayed, discontinue, status,
        )

    def _render_tree(self, products):
        self._render_token += 1
        render_token = self._render_token
        if self._tree.get_children():
            self._tree.delete(*self._tree.get_children())
        self._products_by_iid.clear()
        self._iid_to_url.clear()
        grouped = self._group_products(products)

        def fill():
            if render_token != self._render_token:
                return
            for family_label, items in grouped:
                gap_n = sum(1 for i in items if i.get("gap"))
                exempt_n = sum(1 for i in items if i.get("exempted"))
                disc_n = sum(1 for i in items if i.get("discontinued"))
                summary = f"（{len(items)} 个"
                if gap_n:
                    summary += f"，{gap_n} 待处理"
                if exempt_n:
                    summary += f"，{exempt_n} 已豁免"
                if disc_n:
                    summary += f"，{disc_n} 停产"
                summary += "）"
                parent = self._tree.insert(
                    "", tk.END, text=f"{family_label} {summary}",
                    values=("", "", "", "", "", "", "", ""), tags=("group",), open=True,
                )
                for idx, item in enumerate(items):
                    iid = self._tree.insert(
                        parent, tk.END, image=self._placeholder_photo, text="",
                        values=self._tree_row_values(item), tags=self._row_tag(item, idx),
                    )
                    self._products_by_iid[iid] = item
                    if item.get("image"):
                        self._iid_to_url[iid] = item["image"]
            self._load_visible_images()

        self.root.after_idle(fill)

    def run(self):
        self.root.mainloop()


def main():
    if tk is None:
        print("当前 Python 缺少 Tkinter，无法启动桌面界面。")
        return 1
    PanelApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
