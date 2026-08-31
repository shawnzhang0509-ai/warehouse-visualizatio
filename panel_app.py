"""有货未展示看板 —— 纯本地桌面软件（Tkinter，不走浏览器）。

表格每行内嵌产品缩略图；库存按店面交叉读取 Carbine/Walls/GC。

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

APP_VERSION = "1.1"
ROW_HEIGHT = 58
THUMB = (52, 52)
IMAGE_BATCH = 40
PLACEHOLDER_COLOR = "#d1d5db"
SCROLL_UNITS = 8

# 参考智能库存决策系统的配色
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
C_TEXT = "#1e293b"
C_MUTED = "#64748b"


class PanelApp:
    def __init__(self):
        if tk is None:
            raise RuntimeError("当前 Python 缺少 Tkinter，无法启动桌面界面。")
        self.root = tk.Tk()
        self.root.title("有货未展示看板")
        self.root.geometry("1320x820")
        self.root.minsize(1024, 680)
        self.root.configure(bg=C_BG)

        self._img_cache = {}
        self._pending_images = set()
        self._reload_token = 0
        self._render_token = 0
        self._image_semaphore = threading.Semaphore(6)
        self._products_by_iid = {}
        self._iid_to_url = {}
        self._scroll_after_id = None

        self._tree = None
        self._tree_vscroll = None
        self._placeholder_photo = None
        self._stat_labels = {}

        regions = panel_data.list_regions()
        if not regions:
            raise RuntimeError("region_runner_config.json 中未配置任何地区。")
        default_region = panel_data.default_region()
        stores = panel_data.list_stores(default_region)

        self.store_var = tk.StringVar(value=panel_data.ALL_STORES)
        self.only_gap_var = tk.BooleanVar(value=False)
        self.source_var = tk.StringVar(value="")

        self._region_labels = {r["key"]: r["label"] for r in regions}
        self._setup_styles()
        self._build_ui(stores, regions)
        self.reload()

    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview",
                        rowheight=ROW_HEIGHT,
                        font=("Segoe UI", 10),
                        background="white",
                        fieldbackground="white")
        style.configure("Treeview.Heading",
                        font=("Segoe UI", 10, "bold"),
                        background="#e2e8f0",
                        foreground=C_TEXT)
        style.map("Treeview", background=[("selected", "#bfdbfe")])
        style.configure("TCombobox", padding=4)
        style.configure("Tool.TButton", padding=(10, 4))
        style.configure("Vertical.TScrollbar", width=18, arrowsize=14)

    def _build_ui(self, stores, regions):
        # ── 顶栏（深蓝） ──
        header = tk.Frame(self.root, bg=C_HEADER, padx=16, pady=10)
        header.pack(fill=tk.X)
        tk.Label(header, text="有货未展示看板", bg=C_HEADER, fg="white",
                 font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT)
        tk.Label(header, text=f"v{APP_VERSION}", bg=C_HEADER, fg="#93c5fd",
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(8, 0), pady=(6, 0))
        tk.Label(header, textvariable=self.source_var, bg=C_HEADER, fg="#cbd5e1",
                 font=("Segoe UI", 9)).pack(side=tk.RIGHT)

        # ── 筛选工具栏 ──
        toolbar = tk.Frame(self.root, bg="white", padx=14, pady=10)
        toolbar.pack(fill=tk.X, padx=12, pady=(10, 0))

        tk.Label(toolbar, text="地区", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="w")
        region_values = []
        default_index = 0
        for i, r in enumerate(regions):
            label = r["label"]
            if not r.get("has_latest"):
                label += " (尚无数据)"
            region_values.append(f"{r['key']} {label}")
            if r["key"] == panel_data.default_region():
                default_index = i
        self.region_combo = ttk.Combobox(toolbar, width=18, state="readonly", values=region_values)
        self.region_combo.current(default_index)
        self.region_combo.grid(row=1, column=0, sticky="w", padx=(0, 14), pady=(2, 0))
        self.region_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_region_change())

        tk.Label(toolbar, text="店面", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).grid(
            row=0, column=1, sticky="w")
        self.store_combo = ttk.Combobox(toolbar, width=22, state="readonly",
                                         values=stores, textvariable=self.store_var, height=18)
        self.store_combo.grid(row=1, column=1, sticky="w", padx=(0, 14), pady=(2, 0))
        self.store_combo.bind("<<ComboboxSelected>>", lambda _e: self.reload())

        btn_frame = tk.Frame(toolbar, bg="white")
        btn_frame.grid(row=1, column=2, sticky="w", pady=(2, 0))
        self.reload_btn = ttk.Button(btn_frame, text="刷新数据", style="Tool.TButton",
                                     command=lambda: self.reload(force=True))
        self.reload_btn.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Checkbutton(btn_frame, text="只看有货未展示",
                        variable=self.only_gap_var, command=self.reload).pack(side=tk.LEFT)

        # ── 统计卡片 ──
        cards = tk.Frame(self.root, bg=C_BG, padx=12, pady=10)
        cards.pack(fill=tk.X)
        card_defs = [
            ("gap", "有货未展示", "0", C_CARD_GAP_BG, C_CARD_GAP),
            ("exempted", "同组豁免", "0", C_CARD_EXEMPT_BG, C_CARD_EXEMPT),
            ("in_stock", "有货产品", "0", C_CARD_OK_BG, C_CARD_OK),
            ("rate", "有货率", "-", C_CARD_INFO_BG, C_CARD_INFO),
            ("total", "纳入分析", "0", C_CARD_NEUTRAL_BG, C_CARD_NEUTRAL),
        ]
        for i, (key, title, val, bg, fg) in enumerate(card_defs):
            card = tk.Frame(cards, bg=bg, padx=18, pady=12)
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0 if i == 0 else 6, 0))
            tk.Label(card, text=title, bg=bg, fg=fg, font=("Segoe UI", 9)).pack(anchor="w")
            lbl = tk.Label(card, text=val, bg=bg, fg=fg, font=("Segoe UI", 20, "bold"))
            lbl.pack(anchor="w", pady=(2, 0))
            self._stat_labels[key] = lbl

        self._stock_source_lbl = tk.Label(cards, text="", bg=C_BG, fg=C_MUTED, font=("Segoe UI", 9))
        self._stock_source_lbl.pack(side=tk.RIGHT, padx=8)

        # ── 产品表格（首列图片） ──
        table_wrap = tk.Frame(self.root, bg="white", padx=1, pady=1)
        table_wrap.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        inner = tk.Frame(table_wrap, bg="white")
        inner.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        columns = ("code", "name", "family", "price", "stock", "display", "status")
        self._tree = ttk.Treeview(inner, columns=columns, show="tree headings", selectmode="browse")
        self._tree.heading("#0", text="产品图")
        self._tree.column("#0", width=68, stretch=False, anchor="center")
        headings = {
            "code": ("编码", 100), "name": ("名称", 300), "family": ("系列", 100),
            "price": ("价格", 80), "stock": ("库存", 64), "display": ("展示", 72), "status": ("状态", 120),
        }
        for col, (text, width) in headings.items():
            self._tree.heading(col, text=text)
            anchor = "w" if col in ("code", "name", "family") else "center"
            self._tree.column(col, width=width, anchor=anchor, stretch=(col == "name"))
        self._tree.tag_configure("gap", background=C_ROW_GAP)
        self._tree.tag_configure("exempted", background=C_ROW_EXEMPT)
        self._tree.tag_configure("ok", background=C_ROW_OK)
        self._tree.tag_configure("alt", background=C_ROW_ALT)
        self._tree.tag_configure("group", background="#e2e8f0")

        self._tree_vscroll = ttk.Scrollbar(inner, orient="vertical", command=self._on_tree_yscroll)
        self._tree.configure(yscrollcommand=self._tree_vscroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._tree_vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._placeholder_photo = self._make_placeholder_photo()
        for widget in (inner, table_wrap, self._tree):
            widget.bind("<MouseWheel>", self._on_wheel)
            widget.bind("<Button-4>", lambda _e: self._scroll_tree(-1))
            widget.bind("<Button-5>", lambda _e: self._scroll_tree(1))

    def _make_placeholder_photo(self):
        if Image is not None and ImageTk is not None:
            im = Image.new("RGB", THUMB, PLACEHOLDER_COLOR)
            return ImageTk.PhotoImage(im)
        img = tk.PhotoImage(width=THUMB[0], height=THUMB[1])
        img.put(PLACEHOLDER_COLOR, to=(0, 0, THUMB[0], THUMB[1]))
        return img

    def _on_tree_yscroll(self, *args):
        self._tree.yview(*args)
        self._debounce_visible_images()

    def _debounce_visible_images(self):
        if self._scroll_after_id:
            self.root.after_cancel(self._scroll_after_id)
        self._scroll_after_id = self.root.after(80, self._load_visible_images)

    def _scroll_tree(self, direction):
        if self._tree:
            self._tree.yview_scroll(direction * SCROLL_UNITS, "units")
            self._debounce_visible_images()

    def _on_wheel(self, event):
        if not self._tree:
            return
        if hasattr(event, "delta"):
            step = -1 if event.delta > 0 else 1
        else:
            step = -1
        self._scroll_tree(step)

    def _visible_iids(self):
        if not self._tree:
            return []
        height = max(self._tree.winfo_height(), ROW_HEIGHT)
        seen = []
        y = 0
        while y < height + ROW_HEIGHT * 4:
            iid = self._tree.identify_row(y)
            if not iid:
                y += ROW_HEIGHT
                continue
            if iid in seen:
                y += ROW_HEIGHT
                continue
            if iid not in self._iid_to_url:
                y += ROW_HEIGHT
                continue
            seen.append(iid)
            y += ROW_HEIGHT
        return seen

    def _current_region(self):
        text = self.region_combo.get().strip()
        return text.split(" ")[0] if text else panel_data.default_region()

    def _set_controls_state(self, enabled):
        state = "readonly" if enabled else "disabled"
        normal = tk.NORMAL if enabled else tk.DISABLED
        self.region_combo.configure(state=state)
        self.store_combo.configure(state=state)
        self.reload_btn.configure(state=normal)

    def _set_busy(self, busy):
        self.root.config(cursor="watch" if busy else "")

    def _run_bg(self, worker, on_done):
        def _thread():
            try:
                result = worker()
                err = None
            except Exception as exc:
                result = None
                err = exc
            self.root.after(0, lambda: on_done(err, result))

        threading.Thread(target=_thread, daemon=True).start()

    def _on_region_change(self):
        region = self._current_region()
        panel_data.clear_region_cache(region)
        self._set_controls_state(False)
        self._set_busy(True)

        def worker():
            return panel_data.list_stores(region)

        def done(err, stores):
            self._set_busy(False)
            if err:
                self._set_controls_state(True)
                return
            self.store_combo.configure(values=stores)
            if self.store_var.get() not in stores:
                self.store_var.set(stores[0] if stores else panel_data.ALL_STORES)
            self.reload()

        self._run_bg(worker, done)

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
        safe_url = panel_data.normalize_url(url)
        req = urllib.request.Request(
            safe_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WarehousePanel/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()

    def _schedule_row_image(self, iid, raw, render_token):
        if not raw or raw in self._pending_images:
            return
        cache_key = f"{raw}@{THUMB[0]}x{THUMB[1]}"
        if cache_key in self._img_cache:
            photo = self._img_cache[cache_key]
            if self._tree.exists(iid):
                self._tree.item(iid, image=photo)
            return

        self._pending_images.add(raw)

        def worker():
            photo = None
            try:
                with self._image_semaphore:
                    if str(raw).lower().startswith(("http://", "https://")):
                        data = self._fetch_image_bytes(raw)
                        if Image is not None:
                            im = Image.open(io.BytesIO(data))
                            photo = self._pil_to_photo(im)
                    elif Path(raw).exists() and Image is not None:
                        im = Image.open(raw)
                        photo = self._pil_to_photo(im)
            except Exception:
                photo = None

            def apply():
                self._pending_images.discard(raw)
                if render_token != self._render_token:
                    return
                if photo is not None:
                    self._img_cache[cache_key] = photo
                if self._tree.exists(iid):
                    self._tree.item(iid, image=photo or self._placeholder_photo)

            if self.root.winfo_exists():
                self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _load_visible_images(self):
        if not self._tree:
            return
        render_token = self._render_token
        iids = self._visible_iids()[:IMAGE_BATCH]
        for iid in iids:
            raw = self._iid_to_url.get(iid)
            if raw:
                self._schedule_row_image(iid, raw, render_token)

    def reload(self, force=False):
        self._reload_token += 1
        self._render_token += 1
        self._pending_images.clear()
        token = self._reload_token
        region = self._current_region()
        store = self.store_var.get()
        only_gap = self.only_gap_var.get()

        self._set_controls_state(False)
        self._set_busy(True)

        def worker():
            return panel_data.build_products(
                store=store, only_gap=only_gap, region=region, force_refresh=force,
            )

        def done(err, data):
            if token != self._reload_token:
                return
            self._set_busy(False)
            self._set_controls_state(True)
            if err:
                self._stat_labels["gap"].configure(text="!")
                return
            self._apply_data(data, region)

        self._run_bg(worker, done)

    def _apply_data(self, data, region):
        s = data["summary"]
        src = self._region_labels.get(region, region) if str(data["source"]).startswith("region") else "CSV"
        self.source_var.set(f"数据源：{src}  |  {Path(data['stock_path']).name}")

        def pct(v):
            return "-" if v is None else f"{v:.1f}%"

        self._stat_labels["gap"].configure(text=str(s["not_displayed_count"]))
        self._stat_labels["exempted"].configure(text=str(s.get("exempted_count", 0)))
        self._stat_labels["in_stock"].configure(text=str(s["in_stock_count"]))
        self._stat_labels["rate"].configure(text=pct(s["in_stock_rate"]))
        self._stat_labels["total"].configure(text=str(s["total_non_discontinue"]))
        self._stock_source_lbl.configure(
            text=f"店面：{s['store']}  |  库存来源：{s.get('stock_sources', '-')}"
        )

        products = data["products"]
        self._render_tree(products)

    def _row_tag(self, item, index):
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
            price, stock, displayed, status,
        )

    def _group_products(self, products):
        groups = {}
        order = []
        for item in products:
            label = item.get("exemption_group_label") or item.get("family") or "未分类"
            if label not in groups:
                groups[label] = []
                order.append(label)
            groups[label].append(item)
        return [(label, groups[label]) for label in order]

    def _render_tree(self, products):
        self._render_token += 1
        render_token = self._render_token

        children = self._tree.get_children()
        if children:
            self._tree.delete(*children)
        self._products_by_iid.clear()
        self._iid_to_url.clear()

        grouped = self._group_products(products)

        def fill():
            if render_token != self._render_token:
                return
            for family_label, items in grouped:
                gap_n = sum(1 for i in items if i.get("gap"))
                exempt_n = sum(1 for i in items if i.get("exempted"))
                summary = f"（{len(items)} 个"
                if gap_n:
                    summary += f"，{gap_n} 待处理"
                if exempt_n:
                    summary += f"，{exempt_n} 已豁免"
                summary += "）"
                parent = self._tree.insert(
                    "", tk.END,
                    text=f"{family_label} {summary}",
                    values=("", "", "", "", "", "", ""),
                    tags=("group",),
                    open=True,
                )
                for idx, item in enumerate(items):
                    values = self._tree_row_values(item)
                    tags = self._row_tag(item, idx)
                    iid = self._tree.insert(
                        parent, tk.END,
                        image=self._placeholder_photo,
                        text="",
                        values=values,
                        tags=tags,
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
