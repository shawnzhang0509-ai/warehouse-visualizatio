"""有货未展示看板 —— 纯本地桌面软件（Tkinter，不走浏览器）。

左侧产品列表 + 右侧大图预览；切换店面时复用内存缓存，避免重复读 Excel。

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

DETAIL_THUMB = (280, 210)
PLACEHOLDER_COLOR = "#cbd5e1"

COL_TEXT = "#1f2937"
COL_MUTED = "#6b7280"
COL_OK = "#16a34a"
COL_ERR = "#dc2626"


class PanelApp:
    def __init__(self):
        if tk is None:
            raise RuntimeError("当前 Python 缺少 Tkinter，无法启动桌面界面。")
        self.root = tk.Tk()
        self.root.title("有货未展示看板（本地版）")
        self.root.geometry("1180x780")
        self.root.minsize(960, 620)

        self._img_cache = {}
        self._pending_images = set()
        self._reload_token = 0
        self._render_token = 0
        self._detail_token = 0
        self._image_semaphore = threading.Semaphore(4)
        self._products_by_iid = {}
        self._current_products = []

        self.product_container = None
        self._tree = None
        self._tree_vscroll = None
        self._detail_img = None
        self._detail_title = None
        self._detail_meta = None
        self._detail_status = None
        self._placeholder_photo = None

        regions = panel_data.list_regions()
        if not regions:
            raise RuntimeError("region_runner_config.json 中未配置任何地区。")
        default_region = panel_data.default_region()
        stores = panel_data.list_stores(default_region)

        self.region_var = tk.StringVar(value=default_region)
        self.store_var = tk.StringVar(value=panel_data.ALL_STORES)
        self.only_gap_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="")
        self.source_var = tk.StringVar(value="")
        self._region_labels = {r["key"]: r["label"] for r in regions}

        self._build_ui(stores, regions)
        self._setup_main_view()
        self.reload()

    def _build_ui(self, stores, regions):
        top = ttk.Frame(self.root, padding=(12, 10))
        top.pack(fill=tk.X)

        ttk.Label(top, text="有货未展示看板", font=("Segoe UI", 15, "bold")).pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self.source_var, foreground=COL_MUTED).pack(side=tk.RIGHT)

        bar = ttk.Frame(self.root, padding=(12, 0))
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="地区：").pack(side=tk.LEFT)
        region_values = []
        default_index = 0
        for i, r in enumerate(regions):
            label = r["label"]
            if not r.get("has_latest"):
                label += " (尚无数据)"
            region_values.append(f"{r['key']} {label}")
            if r["key"] == panel_data.default_region():
                default_index = i
        self.region_combo = ttk.Combobox(bar, width=22, state="readonly", values=region_values)
        self.region_combo.current(default_index)
        self.region_combo.pack(side=tk.LEFT, padx=(0, 12))
        self.region_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_region_change())
        ttk.Label(bar, text="店面：").pack(side=tk.LEFT)
        self.store_combo = ttk.Combobox(bar, width=24, state="readonly",
                                         values=stores, textvariable=self.store_var)
        self.store_combo.pack(side=tk.LEFT, padx=(0, 12))
        self.store_combo.bind("<<ComboboxSelected>>", lambda _e: self.reload())
        self.reload_btn = ttk.Button(bar, text="刷新", command=lambda: self.reload(force=True))
        self.reload_btn.pack(side=tk.LEFT)
        ttk.Checkbutton(bar, text="只看有货未展示", variable=self.only_gap_var,
                        command=self.reload).pack(side=tk.LEFT, padx=(12, 0))

        self.summary_lbl = ttk.Label(self.root, textvariable=self.status_var,
                                     padding=(12, 8), font=("Segoe UI", 11))
        self.summary_lbl.pack(fill=tk.X)

        self.product_container = ttk.Frame(self.root)
        self.product_container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

    def _setup_main_view(self):
        paned = ttk.PanedWindow(self.product_container, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        tree_frame = ttk.Frame(paned)
        detail_frame = ttk.Frame(paned, padding=(16, 12))
        paned.add(tree_frame, weight=3)
        paned.add(detail_frame, weight=1)

        columns = ("code", "name", "family", "price", "stock", "display", "status")
        self._tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "code": ("编码", 96), "name": ("名称", 260), "family": ("系列", 88),
            "price": ("价格", 72), "stock": ("库存", 56), "display": ("展示", 64), "status": ("状态", 108),
        }
        for col, (text, width) in headings.items():
            self._tree.heading(col, text=text)
            self._tree.column(col, width=width, anchor="w" if col in ("code", "name", "family") else "center")
        self._tree.tag_configure("gap", background="#fef2f2")
        self._tree_vscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=self._tree_vscroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._tree_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        ttk.Label(detail_frame, text="产品预览", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(detail_frame, text="点击左侧列表查看图片", foreground=COL_MUTED).pack(anchor="w", pady=(0, 8))

        self._detail_img = tk.Label(detail_frame, bg=PLACEHOLDER_COLOR, width=280, height=210)
        self._detail_img.pack(anchor="w", pady=(0, 12))
        self._placeholder_photo = self._make_placeholder_photo()
        self._detail_img.configure(image=self._placeholder_photo)
        self._detail_img.image = self._placeholder_photo

        self._detail_title = ttk.Label(detail_frame, text="—", font=("Segoe UI", 11, "bold"), wraplength=300)
        self._detail_title.pack(anchor="w")
        self._detail_meta = ttk.Label(detail_frame, text="", foreground=COL_MUTED, wraplength=300)
        self._detail_meta.pack(anchor="w", pady=(6, 0))
        self._detail_status = ttk.Label(detail_frame, text="", font=("Segoe UI", 10))
        self._detail_status.pack(anchor="w", pady=(10, 0))

        self.root.bind_all("<MouseWheel>", self._on_wheel)
        self.root.bind_all("<Button-4>", lambda _e: self._on_wheel(-1))
        self.root.bind_all("<Button-5>", lambda _e: self._on_wheel(1))

    def _make_placeholder_photo(self):
        if Image is not None and ImageTk is not None:
            im = Image.new("RGB", DETAIL_THUMB, PLACEHOLDER_COLOR)
            return ImageTk.PhotoImage(im)
        img = tk.PhotoImage(width=DETAIL_THUMB[0], height=DETAIL_THUMB[1])
        img.put(PLACEHOLDER_COLOR, to=(0, 0, DETAIL_THUMB[0], DETAIL_THUMB[1]))
        return img

    def _on_wheel(self, delta):
        if not self._tree:
            return
        if isinstance(delta, int):
            self._tree.yview_scroll(delta, "units")
        else:
            step = -1 if delta.delta > 0 else 1
            self._tree.yview_scroll(step, "units")

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
        self.status_var.set(f"正在加载 {region} 店面列表...")

        def worker():
            return panel_data.list_stores(region)

        def done(err, stores):
            self._set_busy(False)
            if err:
                self.status_var.set(f"加载店面失败：{err}")
                self._set_controls_state(True)
                return
            self.store_combo.configure(values=stores)
            if self.store_var.get() not in stores:
                self.store_var.set(stores[0] if stores else panel_data.ALL_STORES)
            self.reload()

        self._run_bg(worker, done)

    def _pil_to_photo(self, im, size):
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
        im.thumbnail(size)
        return ImageTk.PhotoImage(im)

    def _fetch_image_bytes(self, url):
        safe_url = panel_data.normalize_url(url)
        req = urllib.request.Request(
            safe_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WarehousePanel/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()

    def _load_detail_image(self, item, detail_token):
        raw = item.get("image")
        if not raw:
            self._detail_img.configure(image=self._placeholder_photo)
            self._detail_img.image = self._placeholder_photo
            return

        cached = self._img_cache.get(raw)
        if cached:
            self._detail_img.configure(image=cached)
            self._detail_img.image = cached
            return

        if raw in self._pending_images:
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
                            photo = self._pil_to_photo(im, DETAIL_THUMB)
                    elif Path(raw).exists() and Image is not None:
                        im = Image.open(raw)
                        photo = self._pil_to_photo(im, DETAIL_THUMB)
            except Exception:
                photo = None

            def apply():
                self._pending_images.discard(raw)
                if detail_token != self._detail_token:
                    return
                if photo is not None:
                    self._img_cache[raw] = photo
                    self._detail_img.configure(image=photo)
                    self._detail_img.image = photo
                else:
                    self._detail_img.configure(image=self._placeholder_photo)
                    self._detail_img.image = self._placeholder_photo

            if self.root.winfo_exists():
                self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _on_tree_select(self, _event=None):
        sel = self._tree.selection()
        if not sel:
            return
        item = self._products_by_iid.get(sel[0])
        if not item:
            return
        self._detail_token += 1
        detail_token = self._detail_token

        title = f"{item['code']}  ·  {item.get('name') or '（无名称）'}"
        self._detail_title.configure(text=title)

        meta_parts = [f"系列：{item.get('family') or '—'}"]
        if item.get("price") is not None:
            meta_parts.append(f"价格：{item['price']:,.2f}")
        meta_parts.append(f"库存：{int(item['stock_qty']) if item.get('in_stock') else 0}")
        self._detail_meta.configure(text="    ".join(meta_parts))

        if item.get("gap"):
            status = "★ 有货未展示"
            color = COL_ERR
        elif item.get("discontinued"):
            status = "已停产"
            color = COL_MUTED
        elif item.get("in_stock"):
            status = "有货" + (" · 已展示" if item.get("displayed") else " · 未展示")
            color = COL_OK
        else:
            status = "无货 · 未展示"
            color = COL_MUTED
        self._detail_status.configure(text=status, foreground=color)

        self._load_detail_image(item, detail_token)

    def reload(self, force=False):
        self._reload_token += 1
        self._render_token += 1
        token = self._reload_token
        region = self._current_region()
        store = self.store_var.get()
        only_gap = self.only_gap_var.get()

        self._set_controls_state(False)
        self._set_busy(True)
        self.status_var.set("正在读取数据，请稍候...")

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
                self.status_var.set(f"加载失败：{err}")
                return
            self._apply_data(data, region)

        self._run_bg(worker, done)

    def _apply_data(self, data, region):
        s = data["summary"]
        src = self._region_labels.get(region, region) if str(data["source"]).startswith("region") else "CSV"
        self.source_var.set(f"数据源：{src}    库存：{data['stock_path']}")

        def pct(v):
            return "-" if v is None else f"{v:.1f}%"

        products = data["products"]
        extra = ""
        if len(products) > 200 and not self.only_gap_var.get():
            extra = "    （建议勾选「只看有货未展示」缩小列表）"

        summary = (
            f"店面：{s['store']}    "
            f"有货率 {pct(s['in_stock_rate'])}    "
            f"展示覆盖率 {pct(s['display_coverage_rate'])}    "
            f"有货未展示 {s['not_displayed_count']} 个"
            f"（纳入分析 {s['total_non_discontinue']} 个未停产产品）"
            f"{extra}"
        )
        for msg in data.get("diagnostics") or []:
            if msg.get("level") == "warning":
                summary += f"    ⚠ {msg['message']}"
                break
        self.status_var.set(summary)

        self._current_products = products
        self._render_tree(products)

    def _tree_row_values(self, item):
        price = f"{item['price']:,.2f}" if item.get("price") is not None else "-"
        stock = int(item["stock_qty"]) if item.get("in_stock") else 0
        displayed = "已展示" if item.get("displayed") else "未展示"
        if item.get("gap"):
            status = "★有货未展示"
            tags = ("gap",)
        elif item.get("discontinued"):
            status = "已停产"
            tags = ()
        elif item.get("in_stock"):
            status = "有货"
            tags = ()
        else:
            status = "无货"
            tags = ()
        return (
            item["code"], item.get("name") or "", item.get("family") or "",
            price, stock, displayed, status,
        ), tags

    def _render_tree(self, products):
        self._render_token += 1
        render_token = self._render_token

        children = self._tree.get_children()
        if children:
            self._tree.delete(*children)
        self._products_by_iid.clear()

        rows = [self._tree_row_values(item) for item in products]

        def fill():
            if render_token != self._render_token:
                return
            first_iid = None
            for item, (values, tags) in zip(products, rows):
                iid = self._tree.insert("", tk.END, values=values, tags=tags)
                self._products_by_iid[iid] = item
                if first_iid is None:
                    first_iid = iid
            if first_iid:
                self._tree.selection_set(first_iid)
                self._tree.focus(first_iid)
                self._on_tree_select()
            else:
                self._detail_title.configure(text="没有符合条件的产品")
                self._detail_meta.configure(text="")
                self._detail_status.configure(text="")
                self._detail_img.configure(image=self._placeholder_photo)
                self._detail_img.image = self._placeholder_photo

        self.root.after_idle(fill)

    def run(self):
        self.root.mainloop()


def main():
    if tk is None:
        print("当前 Python 缺少 Tkinter，无法启动桌面界面。")
        print("Windows 自带 Tkinter；Linux 可安装：sudo apt-get install python3-tk")
        return 1
    PanelApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
