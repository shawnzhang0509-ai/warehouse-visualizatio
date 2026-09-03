"""有货未展示看板 —— 纯本地桌面软件（Tkinter，不走浏览器）。

表格每行内嵌产品缩略图；支持搜索、筛选、排序、按系列分组。

运行：
    python panel_app.py          # 或双击 start_panel.bat
"""

import io
import os
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

import panel_data

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except Exception:
    tk = None
    ttk = None
    messagebox = None

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

APP_VERSION = "1.5.6"
ROW_HEIGHT = 62
THUMB = (56, 56)
IMAGE_BATCH = 40
PLACEHOLDER_COLOR = "#d1d5db"
SCROLL_UNITS = 8
MAX_TREE_ROWS = 500
AUTO_EXPAND_GROUPS_THRESHOLD = 80
LOAD_IMAGES = os.getenv("PANEL_LOAD_IMAGES", "").lower() in ("1", "true", "yes")

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
C_ROW_DISC = "#fee2e2"
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
        self._cached_blacklist_meta = {}
        self._cached_data_dir = ""
        self._products_cache = {}
        self._prefix_rendered_for = None
        self._lazy_groups = {}
        self._loaded_full_stock = False
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
        self._stat_hints = {}
        self._stat_cards = {}
        self._quick_filter = None

        regions = panel_data.list_regions()
        if not regions:
            raise RuntimeError("region_runner_config.json 中未配置任何地区。")
        default_region = panel_data.default_region()
        stores = panel_data.list_stores(default_region)

        default_store = next(
            (s for s in stores if s != panel_data.ALL_STORES),
            stores[0] if stores else panel_data.ALL_STORES,
        )
        self.store_var = tk.StringVar(value=default_store)
        self.only_gap_var = tk.BooleanVar(value=False)
        self.only_exempted_var = tk.BooleanVar(value=False)
        self.source_var = tk.StringVar(value="")
        self.search_var = tk.StringVar()
        self.stock_filter_var = tk.StringVar(value="全部")
        self.display_filter_var = tk.StringVar(value="全部")
        self.discontinue_filter_var = tk.StringVar(value="全部")
        self.group_sort_var = tk.StringVar(value="库存总数多到少")
        self.load_images_var = tk.BooleanVar(value=False)
        self.result_count_var = tk.StringVar(value="")
        self._status_var = tk.StringVar(value="")

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
        self.reload_btn.grid(row=1, column=2, sticky="w", padx=(0, 8), pady=(2, 0))
        self.view_img_btn = ttk.Button(toolbar, text="查看图片", style="Tool.TButton",
                                       command=self._open_selected_image)
        self.view_img_btn.grid(row=1, column=3, sticky="w", pady=(2, 0))

        filter_bar = tk.Frame(self.root, bg="white", padx=14, pady=8)
        filter_bar.pack(fill=tk.X, padx=12, pady=(6, 0))

        tk.Label(filter_bar, text="搜索", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        search_entry = ttk.Entry(filter_bar, textvariable=self.search_var, width=28)
        search_entry.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(2, 0))

        filters = [
            ("库存", self.stock_filter_var, ("全部", "有货", "无货"), 1),
            ("展示", self.display_filter_var, ("全部", "已展示", "未展示"), 2),
            ("停产", self.discontinue_filter_var, ("在产", "全部", "已停产"), 3),
            ("组排序", self.group_sort_var, ("字母序", "SKU数量多到少", "库存总数多到少"), 4),
        ]
        for label, var, values, col in filters:
            tk.Label(filter_bar, text=label, bg="white", fg=C_MUTED, font=("Segoe UI", 9)).grid(
                row=0, column=col, sticky="w")
            cb = ttk.Combobox(filter_bar, width=10, state="readonly", textvariable=var, values=values)
            cb.grid(row=1, column=col, sticky="w", padx=(0, 10), pady=(2, 0))
            cb.bind("<<ComboboxSelected>>", lambda _e: self._on_filter_combo_change())

        ttk.Checkbutton(filter_bar, text="只看有货未展示", variable=self.only_gap_var,
                        command=self._on_only_gap_toggle).grid(row=1, column=5, sticky="w", padx=(4, 0))
        ttk.Checkbutton(filter_bar, text="只看同组豁免", variable=self.only_exempted_var,
                        command=self._on_only_exempted_toggle).grid(row=1, column=6, sticky="w", padx=(8, 0))
        ttk.Checkbutton(filter_bar, text="行内缩略图", variable=self.load_images_var,
                        command=self._on_toggle_inline_images).grid(row=1, column=7, sticky="w", padx=(8, 0))

        tk.Label(filter_bar, textvariable=self.result_count_var, bg="white", fg=C_MUTED,
                 font=("Segoe UI", 9)).grid(row=1, column=8, sticky="e", padx=(12, 0))
        tk.Label(filter_bar, textvariable=self._status_var, bg="white", fg=C_CARD_GAP,
                 font=("Segoe UI", 9)).grid(row=0, column=8, sticky="e", padx=(12, 0))
        filter_bar.columnconfigure(8, weight=1)

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
                hint_text = "含在产与停产 · 点击筛选" if key == "gap" else "点击筛选"
                hint = tk.Label(card, text=hint_text, bg=bg, fg=fg, font=("Segoe UI", 8), cursor="hand2")
                hint.pack(anchor="w")
                self._stat_hints[key] = hint
                for w in (card, title_lbl, val_lbl, hint):
                    w.bind("<Button-1>", lambda _e, k=key: self._on_stat_card_click(k))
            self._stat_labels[key] = val_lbl
            self._stat_cards[key] = card

        self._stock_source_lbl = tk.Label(cards, text="", bg=C_BG, fg=C_MUTED, font=("Segoe UI", 9))
        self._stock_source_lbl.pack(side=tk.RIGHT, padx=8)

        info_row = tk.Frame(self.root, bg=C_BG)
        info_row.pack(fill=tk.X, padx=12, pady=(0, 6))
        self._blacklist_lbl = tk.Label(
            info_row,
            text="黑名单：加载中…",
            bg=C_BG, fg=C_MUTED, font=("Segoe UI", 9), anchor="w", justify=tk.LEFT,
        )
        self._blacklist_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            info_row,
            text="黑名单文件列名：sku / 编码 / ProductCode",
            bg=C_BG, fg="#94a3b8", font=("Segoe UI", 8),
        ).pack(side=tk.RIGHT, padx=(8, 0))

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
        inner.grid_rowconfigure(0, weight=1)
        inner.grid_columnconfigure(0, weight=1)

        columns = ("code", "name", "family", "price", "stock", "display", "discontinue", "status")
        self._tree = ttk.Treeview(inner, columns=columns, show="tree headings", selectmode="browse")
        self._tree.heading("#0", text="产品图")
        self._tree.column("#0", width=72, minwidth=68, stretch=False, anchor="center")
        headings = {
            "code": ("编码", 104), "name": ("名称", 280), "family": ("系列", 92),
            "price": ("价格", 76), "stock": ("库存", 108), "display": ("展示", 58),
            "discontinue": ("停产", 52), "status": ("状态", 136),
        }
        for col, (text, width) in headings.items():
            self._tree.heading(col, text=text, command=lambda c=col: self._on_sort_column(c))
            anchor = "w" if col in ("code", "name", "family") else "center"
            stretch = col == "name"
            min_w = 72 if col == "name" else width
            self._tree.column(col, width=width, minwidth=min_w, anchor=anchor, stretch=stretch)

        for tag, bg in (("gap", C_ROW_GAP), ("exempted", C_ROW_EXEMPT), ("ok", C_ROW_OK),
                        ("alt", C_ROW_ALT), ("discontinued", C_ROW_DISC), ("group", "#e2e8f0")):
            self._tree.tag_configure(tag, background=bg)
        self._tree.tag_configure("group", font=("Segoe UI", 10, "bold"))

        self._tree_vscroll = ttk.Scrollbar(inner, orient="vertical", command=self._on_tree_yscroll)
        self._tree.configure(yscrollcommand=self._tree_vscroll.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        self._tree_vscroll.grid(row=0, column=1, sticky="ns")
        self._tree.bind("<<TreeviewOpen>>", self._on_tree_group_open)
        self._tree.bind("<Double-1>", self._on_tree_double_click)

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
        store = self.store_combo.get() if self.store_combo else self.store_var.get()
        return store != panel_data.ALL_STORES

    def _on_filter_combo_change(self):
        disc_f = self.discontinue_filter_var.get()
        if not panel_data.EAGER_DISCONTINUED_STOCK:
            need_full = self.discontinue_filter_var.get() in ("全部", "已停产")
            if need_full != self._loaded_full_stock:
                if need_full:
                    if self.only_gap_var.get():
                        self.only_gap_var.set(False)
                    if self.only_exempted_var.get():
                        self.only_exempted_var.set(False)
                    self._quick_filter = None
                    self._status_var.set(
                        "正在加载全部数据（含停产），请稍候…"
                        if disc_f == "全部" else "正在加载停产数据，请稍候…"
                    )
                self.reload()
                return
        if disc_f == "已停产":
            if self.only_gap_var.get():
                self.only_gap_var.set(False)
            if self.only_exempted_var.get():
                self.only_exempted_var.set(False)
            self._quick_filter = None
        elif self._quick_filter == "raw_gap":
            if not (
                self.stock_filter_var.get() == "有货"
                and self.display_filter_var.get() == "未展示"
                and self.discontinue_filter_var.get() == "全部"
            ):
                self._quick_filter = None
        self._refresh_view()

    def _on_only_gap_toggle(self):
        if self.only_gap_var.get() and self.discontinue_filter_var.get() == "已停产":
            self.discontinue_filter_var.set("在产")
            if not panel_data.EAGER_DISCONTINUED_STOCK:
                self.reload()
                return
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
            self.only_gap_var.set(False)
            is_raw_gap_view = (
                self.stock_filter_var.get() == "有货"
                and self.display_filter_var.get() == "未展示"
                and self.discontinue_filter_var.get() == "全部"
                and self._quick_filter == "raw_gap"
            )
            if is_raw_gap_view:
                self.stock_filter_var.set("全部")
                self.display_filter_var.set("全部")
                self.discontinue_filter_var.set("全部")
                self._quick_filter = None
            else:
                self.stock_filter_var.set("有货")
                self.display_filter_var.set("未展示")
                self.discontinue_filter_var.set("全部")
                self._quick_filter = "raw_gap"
        elif key == "exempted":
            self.only_gap_var.set(False)
            self.only_exempted_var.set(not self.only_exempted_var.get())
            self._quick_filter = "exempted" if self.only_exempted_var.get() else None
        elif key == "in_stock":
            self.stock_filter_var.set("无货" if self.stock_filter_var.get() == "有货" else "有货")
        if (
            not panel_data.EAGER_DISCONTINUED_STOCK
            and (self.discontinue_filter_var.get() in ("全部", "已停产"))
            != self._loaded_full_stock
        ):
            self.reload()
            return
        self._refresh_view()

    def _update_stat_card_highlight(self):
        highlights = {
            "gap": (
                self.stock_filter_var.get() == "有货"
                and self.display_filter_var.get() == "未展示"
                and self.discontinue_filter_var.get() == "全部"
                and self._quick_filter == "raw_gap"
            ),
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
        self._products_cache = {}
        self._prefix_rendered_for = None
        self._set_controls_state(False)
        self._set_busy(True)

        def done(err, stores):
            self._set_busy(False)
            self._set_controls_state(True)
            if err:
                return
            self.store_combo.configure(values=stores)
            default_store = next(
                (s for s in stores if s != panel_data.ALL_STORES),
                stores[0] if stores else panel_data.ALL_STORES,
            )
            if self.store_var.get() not in stores:
                self.store_var.set(default_store)
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

    def _images_enabled(self):
        return self.load_images_var.get() or LOAD_IMAGES

    def _on_toggle_inline_images(self):
        if self._images_enabled():
            self._load_visible_images()
        elif self._tree:
            for iid in self._products_by_iid:
                if self._tree.exists(iid):
                    self._tree.item(iid, image=self._placeholder_photo)

    def _open_selected_image(self):
        if not self._tree or messagebox is None:
            return
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("查看图片", "请先选中一行产品（可双击分组展开）。")
            return
        iid = sel[0]
        if iid in self._lazy_groups:
            self._populate_lazy_group(iid)
            messagebox.showinfo("查看图片", "已展开分组，请再选中具体产品行。")
            return
        item = self._products_by_iid.get(iid)
        if not item:
            messagebox.showinfo("查看图片", "请选中具体产品行，不要选分组标题。")
            return
        self._open_image_for_item(item)

    def _image_url_for_item(self, item):
        if item.get("image"):
            return item["image"]
        if self._cached_data_dir:
            return panel_data.resolve_product_image(item, self._cached_data_dir)
        return None

    def _open_image_for_item(self, item):
        if messagebox is None:
            return
        raw = self._image_url_for_item(item)
        code = item.get("code") or "产品"
        if not raw:
            messagebox.showinfo("查看图片", f"{code} 没有图片路径/URL。")
            return
        text = str(raw).strip()
        if text.lower().startswith(("http://", "https://")):
            webbrowser.open(panel_data.normalize_url(text))
            return
        path = Path(text)
        if not path.is_absolute():
            path = Path(panel_data.ROOT_DIR) / path
        if path.is_file():
            if Image is not None and ImageTk is not None:
                self._show_image_window(path, code)
            else:
                webbrowser.open(path.as_uri())
            return
        messagebox.showinfo("查看图片", f"找不到图片文件：\n{text}")

    def _show_image_window(self, path, title):
        win = tk.Toplevel(self.root)
        win.title(f"{title} - 产品图")
        win.transient(self.root)
        try:
            im = Image.open(path)
            im.thumbnail((720, 720))
            photo = ImageTk.PhotoImage(im)
            lbl = tk.Label(win, image=photo)
            lbl.image = photo
            lbl.pack(padx=8, pady=8)
        except Exception as exc:
            tk.Label(win, text=f"无法打开图片：{exc}").pack(padx=12, pady=12)

    def _on_tree_double_click(self, event):
        if not self._tree:
            return
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        if iid in self._lazy_groups:
            self._populate_lazy_group(iid)
            return
        item = self._products_by_iid.get(iid)
        if item:
            self._open_image_for_item(item)

    def _load_visible_images(self):
        if not self._images_enabled() or not self._tree:
            return
        token = self._render_token
        for iid in self._visible_iids()[:IMAGE_BATCH]:
            ref = self._iid_to_url.get(iid)
            if not ref:
                continue
            if isinstance(ref, dict):
                raw = self._image_url_for_item(ref)
            else:
                raw = ref
            if raw:
                self._schedule_row_image(iid, raw, token)

    def _disc_matches(self, product, disc_f):
        if disc_f == "在产":
            return not product.get("discontinued")
        if disc_f == "已停产":
            return bool(product.get("discontinued"))
        return True

    def _compute_gap_stats(self, products, disc_f):
        not_displayed = [
            p for p in products
            if p.get("in_stock") and not p.get("displayed") and not p.get("exempted")
            and self._disc_matches(p, disc_f)
        ]
        pending = [p for p in not_displayed if p.get("gap")]
        exempted = [
            p for p in products
            if p.get("exempted") and p.get("in_stock") and not p.get("displayed")
            and self._disc_matches(p, disc_f)
        ]
        active_nd = [p for p in not_displayed if not p.get("discontinued")]
        disc_nd = [p for p in not_displayed if p.get("discontinued")]

        if disc_f == "在产":
            hint = f"待处理 {len(pending)} · 已豁免 {len(exempted)} · 点击筛选"
        elif disc_f == "已停产":
            hint = "停产有货未展示 · 点击筛选"
        else:
            pending_active = len([p for p in pending if not p.get("discontinued")])
            hint = (
                f"在产 {len(active_nd)}（待处理 {pending_active}）"
                f" · 停产 {len(disc_nd)} · 点击筛选"
            )
        return {
            "main_count": len(not_displayed),
            "hint": hint,
            "pending": len(pending),
            "exempted": len(exempted),
        }

    def _show_loading_state(self, message="正在计算店面数据…"):
        self._status_var.set(message)
        self.result_count_var.set("")
        for key in ("gap", "exempted", "in_stock"):
            if key in self._stat_labels:
                self._stat_labels[key].configure(text="…", font=("Segoe UI", 14))
        self._lazy_groups.clear()
        if self._tree and self._tree.get_children():
            self._tree.delete(*self._tree.get_children())
        self._products_by_iid.clear()
        self._iid_to_url.clear()

    def _update_blacklist_label(self):
        if not getattr(self, "_blacklist_lbl", None):
            return
        meta = self._cached_blacklist_meta or {}
        s = self._cached_summary or {}
        count = meta.get("blacklist_count", s.get("blacklist_count", 0))
        region = self._current_region()
        expected = Path(meta.get("blacklist_expected_path") or panel_data.expected_blacklist_path(region))
        found = meta.get("blacklist_file_found", False)
        path = meta.get("blacklist_path")
        if found and path:
            fname = Path(path).name
            text = f"黑名单：已排除 {count} 个 SKU  |  文件：{fname}（{Path(path).parent.name}/）"
            fg = C_CARD_GAP if count else C_MUTED
        else:
            text = (
                f"黑名单：当前 0 个  |  未找到文件，可在以下路径新建 blacklist.csv：{expected}"
            )
            fg = C_MUTED
        self._blacklist_lbl.configure(text=text, fg=fg)

    def _apply_loaded_data(self, data, region):
        self._cached_products = data["products"]
        self._cached_summary = data["summary"]
        self._cached_blacklist_meta = {
            "blacklist_count": data.get("blacklist_count", 0),
            "blacklist_path": data.get("blacklist_path"),
            "blacklist_file_found": data.get("blacklist_file_found", False),
            "blacklist_expected_path": data.get(
                "blacklist_expected_path", str(panel_data.expected_blacklist_path(region))
            ),
        }
        self._cached_data_dir = data.get("data_dir") or ""
        fmt = data.get("data_format", "")
        src = self._region_labels.get(region, region)
        src_line = f"数据源：{src}  |  {Path(data['stock_path']).name}"
        if fmt:
            src_line += f"（{fmt}）"
        self.source_var.set(src_line)
        self._update_blacklist_label()
        self._prefix_rendered_for = None
        self._loaded_full_stock = (
            panel_data.EAGER_DISCONTINUED_STOCK
            or self.discontinue_filter_var.get() in ("全部", "已停产")
        )
        self._refresh_view()

    def reload(self, force=False):
        self._reload_token += 1
        token = self._reload_token
        region = self._current_region()
        store = self.store_combo.get() if self.store_combo else self.store_var.get()
        eager = panel_data.EAGER_DISCONTINUED_STOCK
        include_disc = True if eager else self.discontinue_filter_var.get() in ("全部", "已停产")
        cache_key = (region, store) if eager else (region, store, include_disc)

        if force:
            self._products_cache = {}
            panel_data.clear_region_cache(region)

        if not force and cache_key in self._products_cache:
            self._apply_loaded_data(self._products_cache[cache_key], region)
            return

        loading_msg = "正在计算店面数据…"
        if eager or include_disc:
            loading_msg += "（含停产）"
        self._show_loading_state(loading_msg)
        self._set_controls_state(False)
        self._set_busy(True)

        def worker():
            return panel_data.build_products(
                store=store, only_gap=False, include_discontinued=True if eager else include_disc,
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
            self._products_cache[cache_key] = data
            self._apply_loaded_data(data, region)

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
            if (
                stock_f == "有货"
                and display_f == "未展示"
                and not only_exempted
                and not only_gap
                and p.get("exempted")
            ):
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

    def _group_stock_total(self, items):
        return sum(float(i.get("stock_qty") or 0) for i in items)

    def _group_products(self, products):
        groups = {}
        for item in products:
            label = item.get("exemption_group_label") or item.get("family") or "未分类"
            groups.setdefault(label, []).append(item)

        result = []
        for label, items in groups.items():
            result.append((label, self._sort_products(items)))

        sort_mode = self.group_sort_var.get()
        if sort_mode in ("数量多到少", "SKU数量多到少"):
            result.sort(key=lambda x: (-len(x[1]), x[0].lower()))
        elif sort_mode == "库存总数多到少":
            result.sort(key=lambda x: (-self._group_stock_total(x[1]), -len(x[1]), x[0].lower()))
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
        disc_f = self.discontinue_filter_var.get()

        def pct(v):
            return "-" if v is None else f"{v:.1f}%"

        if store_specific:
            gap_stats = self._compute_gap_stats(self._cached_products, disc_f)
            self._stat_labels["gap"].configure(
                text=str(gap_stats["main_count"]), font=("Segoe UI", 18, "bold"),
            )
            if "gap" in self._stat_hints:
                self._stat_hints["gap"].configure(text=gap_stats["hint"])
            exempted_n = sum(
                1 for p in self._cached_products
                if p.get("exempted") and self._disc_matches(p, disc_f)
            )
            self._stat_labels["exempted"].configure(text=str(exempted_n), font=("Segoe UI", 18, "bold"))
        else:
            self._stat_labels["gap"].configure(text="请选择店面", font=("Segoe UI", 11, "bold"))
            if "gap" in self._stat_hints:
                self._stat_hints["gap"].configure(text="含在产与停产 · 点击筛选")
            self._stat_labels["exempted"].configure(text="请选择店面", font=("Segoe UI", 11, "bold"))
        self._stat_labels["in_stock"].configure(text=str(s.get("in_stock_count", 0)))
        self._stat_labels["rate"].configure(text=pct(s.get("in_stock_rate")))
        self._stat_labels["total"].configure(text=str(s.get("total_non_discontinue", 0)))
        self._stock_source_lbl.configure(
            text=f"店面：{s.get('store', '-')}  |  库存来源：{s.get('stock_sources', '-')}"
        )
        self._update_blacklist_label()
        self._update_stat_card_highlight()

        filtered = self._apply_client_filters(self._cached_products)
        active_total = sum(1 for p in self._cached_products if not p.get("discontinued"))
        disc_total = sum(1 for p in self._cached_products if p.get("discontinued"))
        if disc_f == "已停产":
            self.result_count_var.set(f"显示 {len(filtered)} / 停产 {disc_total} 条")
        elif disc_f == "在产":
            self.result_count_var.set(f"显示 {len(filtered)} / 在产 {active_total} 条")
        elif disc_f == "全部":
            total = active_total + disc_total
            self.result_count_var.set(
                f"显示 {len(filtered)} / 全部 {total} 条（在产 {active_total} + 停产 {disc_total}）"
            )
        else:
            self.result_count_var.set(f"显示 {len(filtered)} 条")
        if disc_f == "已停产" and len(filtered) == 0 and disc_total > 0:
            if self.stock_filter_var.get() == "有货":
                self.result_count_var.set(
                    f"显示 0 / 停产 {disc_total} 条（可尝试将「库存」改为「全部」）"
                )
        self._status_var.set(f"正在渲染 {len(filtered)} 条…")
        self.root.update_idletasks()
        self._render_tree(filtered)
        prefix_key = (s.get("region"), s.get("store"))
        if prefix_key != self._prefix_rendered_for:
            self.root.after_idle(
                lambda: self._render_prefix_table_deferred(prefix_key, store_specific)
            )
        else:
            self._status_var.set(
                f"就绪 · {len(filtered)} 条"
                + ("" if self._images_enabled() else " · 双击行或点「查看图片」")
            )

    def _render_prefix_table_deferred(self, prefix_key, store_specific):
        if prefix_key != (self._cached_summary.get("region"), self._cached_summary.get("store")):
            return
        self._render_prefix_table(store_specific)
        self._prefix_rendered_for = prefix_key
        n = len(self._apply_client_filters(self._cached_products))
        self._status_var.set(
            f"就绪 · {n} 条"
            + ("" if self._images_enabled() else " · 可点「查看图片」")
        )

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
        if item.get("exempted"):
            return ("exempted",)
        if item.get("gap"):
            return ("gap",)
        if item.get("discontinued"):
            return ("discontinued",)
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
            if item.get("in_stock") and not item.get("displayed"):
                status = "有货未展示(停产)"
            else:
                status = "已停产"
        elif item.get("in_stock"):
            status = "有货"
        else:
            status = "无货"
        return (
            item["code"], item.get("name") or "", item.get("family") or "",
            price, stock, displayed, discontinue, status,
        )

    def _insert_group_children(self, parent, items, render_token):
        if render_token != self._render_token:
            return
        for idx, item in enumerate(items):
            iid = self._tree.insert(
                parent, tk.END, image=self._placeholder_photo, text="",
                values=self._tree_row_values(item), tags=self._row_tag(item, idx),
            )
            self._products_by_iid[iid] = item
            url = self._image_url_for_item(item)
            if url:
                self._iid_to_url[iid] = url
            elif item.get("image_raw"):
                self._iid_to_url[iid] = item

    def _populate_lazy_group(self, iid):
        if iid not in self._lazy_groups:
            return
        items, render_token = self._lazy_groups.pop(iid)
        self._insert_group_children(iid, items, render_token)
        self._debounce_visible_images()

    def _on_tree_group_open(self, _event=None):
        if not self._tree:
            return
        for iid in list(self._lazy_groups):
            if self._tree.exists(iid) and self._tree.item(iid, "open"):
                self._populate_lazy_group(iid)

    def _render_tree(self, products):
        self._render_token += 1
        render_token = self._render_token
        self._lazy_groups.clear()
        if self._tree.get_children():
            self._tree.delete(*self._tree.get_children())
        self._products_by_iid.clear()
        self._iid_to_url.clear()

        if len(products) > MAX_TREE_ROWS:
            self._render_tree_flat(products, render_token)
            return

        grouped = self._group_products(products)
        store_specific = self._cached_summary.get(
            "store_specific", self._is_store_selected()
        )

        def insert_group(family_label, items):
            gap_n = sum(1 for i in items if i.get("gap"))
            exempt_n = sum(1 for i in items if i.get("exempted"))
            disc_n = sum(1 for i in items if i.get("discontinued"))
            summary = f"（{len(items)} 个"
            if store_specific:
                stock_total = int(self._group_stock_total(items))
                summary += f"，库存合计 {stock_total}"
            if gap_n:
                summary += f"，{gap_n} 待处理"
            if exempt_n:
                summary += f"，{exempt_n} 已豁免"
            if disc_n:
                summary += f"，{disc_n} 停产"
            summary += "）"
            has_attention = any(i.get("gap") or i.get("exempted") for i in items)
            expand_now = has_attention or len(products) <= AUTO_EXPAND_GROUPS_THRESHOLD
            label_text = f"{'▸ ' if not expand_now else ''}{family_label} {summary}"
            parent = self._tree.insert(
                "", tk.END, text="",
                values=("", label_text, "", "", "", "", "", ""),
                tags=("group",), open=expand_now,
            )
            if expand_now:
                self._insert_group_children(parent, items, render_token)
            else:
                self._lazy_groups[parent] = (items, render_token)
            return parent

        def fill_batch(start=0):
            if render_token != self._render_token:
                return
            end = min(start + 40, len(grouped))
            for family_label, items in grouped[start:end]:
                insert_group(family_label, items)
            if end < len(grouped):
                self.root.after(1, lambda s=end: fill_batch(s))
            else:
                self._load_visible_images()

        if grouped:
            self.root.after_idle(lambda: fill_batch(0))
        else:
            self._load_visible_images()

    def _render_tree_flat(self, products, render_token):
        """行数较多时用平铺列表，避免创建上千个分组节点卡死界面。"""
        shown = products[:MAX_TREE_ROWS]
        for idx, item in enumerate(shown):
            if render_token != self._render_token:
                return
            iid = self._tree.insert(
                "", tk.END, image=self._placeholder_photo, text="",
                values=self._tree_row_values(item), tags=self._row_tag(item, idx),
            )
            self._products_by_iid[iid] = item
            url = self._image_url_for_item(item)
            if url:
                self._iid_to_url[iid] = url
            elif item.get("image_raw"):
                self._iid_to_url[iid] = item
        if len(products) > MAX_TREE_ROWS:
            self._tree.insert(
                "", tk.END, text="",
                values=(
                    "",
                    f"… 还有 {len(products) - MAX_TREE_ROWS} 条未显示，请缩小筛选范围",
                    "", "", "", "", "", "",
                ),
                tags=("group",),
            )
        self._load_visible_images()

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
