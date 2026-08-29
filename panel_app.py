"""有货未展示看板 —— 纯本地桌面软件（Tkinter，不走浏览器）。

逐个产品展示：图片、是否有货、在所选店面是否展示；
可切换不同店面（例如 Onehunga），并高亮"有货但未展示"的产品。

运行：
    python panel_app.py          # 或双击 start_panel.bat
数据源见 panel_data.py（默认用 sample_data/ 示例数据）。
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

THUMB = (116, 84)
CARD_MODE_LIMIT = 80        # 超过则用表格，避免大量卡片频闪
CARD_RENDER_BATCH = 40
IMAGE_LOAD_LIMIT = 40       # 卡片模式下限制图片加载数

FAMILY_COLORS = {
    "Sofa": "#2563eb", "Bed": "#16a34a", "Dining": "#d97706",
    "Mattress": "#7c3aed", "Chair": "#db2777",
}

COL_TEXT = "#1f2937"
COL_MUTED = "#6b7280"
COL_OK = "#16a34a"
COL_ERR = "#dc2626"
COL_GAP_BG = "#fef2f2"
COL_ROW_BG = "#ffffff"
COL_BORDER = "#e5e7eb"


class PanelApp:
    def __init__(self):
        if tk is None:
            raise RuntimeError("当前 Python 缺少 Tkinter，无法启动桌面界面。")
        self.root = tk.Tk()
        self.root.title("有货未展示看板（本地版）")
        self.root.geometry("1024x760")
        self.root.minsize(880, 600)

        self._img_cache = {}
        self._placeholder_cache = {}
        self._pending_images = set()
        self._reload_token = 0
        self._render_token = 0
        self._summary_text = ""
        self._image_semaphore = threading.Semaphore(4)
        self._image_load_budget = 0
        self._rendering = False
        self._view_mode = None  # "card" | "tree"
        self._tree_vscroll = None

        self.product_container = None
        self.canvas = None
        self.list_frame = None
        self._tree = None
        self._on_frame_configure = None

        regions = panel_data.list_regions()
        default_region = panel_data.SAMPLE_REGION
        stores = panel_data.list_stores(default_region)
        default_store = "Walls Road" if "Walls Road" in stores else (
            "Onehunga" if "Onehunga" in stores else panel_data.ALL_STORES
        )

        self.region_var = tk.StringVar(value=default_region)
        self.store_var = tk.StringVar(value=default_store)
        self.only_gap_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="")
        self.source_var = tk.StringVar(value="")
        self._region_labels = {r["key"]: r["label"] for r in regions}

        self._build_ui(stores, regions)
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
        for r in regions:
            label = r["label"]
            if r["key"] != panel_data.SAMPLE_REGION and not r.get("has_latest"):
                label += " (尚无数据)"
            region_values.append(f"{r['key']} {label}")
        self.region_combo = ttk.Combobox(bar, width=22, state="readonly", values=region_values)
        self.region_combo.current(0)
        self.region_combo.pack(side=tk.LEFT, padx=(0, 12))
        self.region_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_region_change())
        ttk.Label(bar, text="店面：").pack(side=tk.LEFT)
        self.store_combo = ttk.Combobox(bar, width=24, state="readonly",
                                         values=stores, textvariable=self.store_var)
        self.store_combo.pack(side=tk.LEFT, padx=(0, 12))
        self.store_combo.bind("<<ComboboxSelected>>", lambda _e: self.reload())
        self.reload_btn = ttk.Button(bar, text="刷新", command=self.reload)
        self.reload_btn.pack(side=tk.LEFT)
        ttk.Checkbutton(bar, text="只看有货未展示", variable=self.only_gap_var,
                        command=self.reload).pack(side=tk.LEFT, padx=(12, 0))

        self.summary_lbl = ttk.Label(self.root, textvariable=self.status_var,
                                     padding=(12, 8), font=("Segoe UI", 11))
        self.summary_lbl.pack(fill=tk.X)

        self.product_container = ttk.Frame(self.root)
        self.product_container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        self._setup_card_view()

        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", lambda _e: self._on_wheel(-1))
        self.canvas.bind_all("<Button-5>", lambda _e: self._on_wheel(1))

    def _setup_card_view(self):
        if self._view_mode == "card" and self.canvas is not None:
            return
        self._clear_product_container()
        self._view_mode = "card"
        self.canvas = tk.Canvas(self.product_container, highlightthickness=0, background="#f3f6fb")
        vscroll = ttk.Scrollbar(self.product_container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.list_frame = tk.Frame(self.canvas, background="#f3f6fb")
        self._win = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self._on_frame_configure = lambda _e: self._update_scrollregion()
        self.list_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))

    def _setup_tree_view(self):
        if self._view_mode == "tree" and self._tree is not None:
            return
        self._clear_product_container()
        self._view_mode = "tree"
        columns = ("code", "name", "family", "price", "stock", "display", "status")
        self._tree = ttk.Treeview(self.product_container, columns=columns, show="headings", selectmode="browse")
        headings = {
            "code": ("编码", 100), "name": ("名称", 280), "family": ("系列", 100),
            "price": ("价格", 80), "stock": ("库存", 70), "display": ("展示", 70), "status": ("状态", 120),
        }
        for col, (text, width) in headings.items():
            self._tree.heading(col, text=text)
            self._tree.column(col, width=width, anchor="w" if col in ("code", "name", "family") else "center")
        self._tree.tag_configure("gap", background="#fef2f2")
        self._tree_vscroll = ttk.Scrollbar(self.product_container, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=self._tree_vscroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._tree_vscroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _clear_product_container(self):
        for child in self.product_container.winfo_children():
            child.destroy()
        self.canvas = None
        self.list_frame = None
        self._tree = None
        self._tree_vscroll = None

    def _clear_card_rows(self):
        if not self.list_frame:
            return
        for w in self.list_frame.winfo_children():
            w.destroy()

    def _clear_tree_rows(self):
        if not self._tree:
            return
        children = self._tree.get_children()
        if children:
            self._tree.delete(*children)

    def _update_scrollregion(self):
        if self._rendering or not self.canvas:
            return
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_wheel(self, delta):
        if self._view_mode == "card" and self.canvas:
            if isinstance(delta, int):
                self.canvas.yview_scroll(delta, "units")
            else:
                step = -1 if delta.delta > 0 else 1
                self.canvas.yview_scroll(step, "units")
        elif self._view_mode == "tree" and self._tree:
            if isinstance(delta, int):
                self._tree.yview_scroll(delta, "units")
            else:
                step = -1 if delta.delta > 0 else 1
                self._tree.yview_scroll(step, "units")

    def _current_region(self):
        text = self.region_combo.get().strip()
        return text.split(" ")[0] if text else panel_data.SAMPLE_REGION

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
        return ImageTk.PhotoImage(im)

    def _fetch_image_bytes(self, url):
        safe_url = panel_data.normalize_url(url)
        req = urllib.request.Request(
            safe_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WarehousePanel/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()

    def _placeholder(self, item):
        family = item.get("family", "")
        color = FAMILY_COLORS.get(family, "#64748b")
        if color in self._placeholder_cache:
            return self._placeholder_cache[color]
        img = tk.PhotoImage(width=THUMB[0], height=THUMB[1])
        img.put(color, to=(0, 0, THUMB[0], THUMB[1]))
        self._placeholder_cache[color] = img
        return img

    def _schedule_image_load(self, raw, item, img_label, render_token):
        if not raw or raw in self._img_cache or raw in self._pending_images:
            return
        if self._image_load_budget <= 0:
            return
        self._image_load_budget -= 1
        self._pending_images.add(raw)
        threading.Thread(
            target=self._load_image_async,
            args=(raw, item, img_label, render_token),
            daemon=True,
        ).start()

    def _load_image_async(self, raw, item, img_label, render_token):
        photo = None
        try:
            with self._image_semaphore:
                if str(raw).lower().startswith(("http://", "https://")):
                    data = self._fetch_image_bytes(raw)
                    if Image is not None:
                        im = Image.open(io.BytesIO(data))
                        im.thumbnail(THUMB)
                        photo = self._pil_to_photo(im)
                elif Path(raw).exists() and Image is not None:
                    im = Image.open(raw)
                    im.thumbnail(THUMB)
                    photo = self._pil_to_photo(im)
        except Exception:
            photo = None

        def apply():
            self._pending_images.discard(raw)
            if render_token != self._render_token:
                return
            if not img_label.winfo_exists():
                return
            if photo is not None:
                self._img_cache[raw] = photo
                img_label.configure(image=photo)
                img_label.image = photo

        if self.root.winfo_exists():
            self.root.after(0, apply)

    def reload(self):
        self._reload_token += 1
        self._render_token += 1
        self._pending_images.clear()
        token = self._reload_token
        region = self._current_region()
        store = self.store_var.get()
        only_gap = self.only_gap_var.get()

        self._set_controls_state(False)
        self._set_busy(True)
        self.status_var.set("正在读取数据，请稍候...")
        # 保留当前列表内容直到新数据就绪，避免清空后频闪

        def worker():
            return panel_data.build_products(store=store, only_gap=only_gap, region=region)

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
        if data["source"] == "sample":
            src = "示例数据（占位图，非真照片）"
        elif str(data["source"]).startswith("region-"):
            src = self._region_labels.get(region, region)
        else:
            src = "CSV"
        self.source_var.set(f"数据源：{src}    库存：{data['stock_path']}")

        def pct(v):
            return "-" if v is None else f"{v:.1f}%"

        products = data["products"]
        use_tree = len(products) > CARD_MODE_LIMIT
        extra = ""
        if use_tree:
            extra = "    （表格模式；勾选「只看有货未展示」可缩小范围）"
        elif len(products) > CARD_MODE_LIMIT // 2 and not self.only_gap_var.get():
            extra = f"    （共 {len(products)} 条，建议勾选「只看有货未展示」）"

        self._summary_text = (
            f"店面：{s['store']}    "
            f"有货率 {pct(s['in_stock_rate'])}    "
            f"展示覆盖率 {pct(s['display_coverage_rate'])}    "
            f"有货未展示 {s['not_displayed_count']} 个"
            f"（纳入分析 {s['total_non_discontinue']} 个未停产产品）"
            f"{extra}"
        )
        self.status_var.set(self._summary_text)

        for msg in data.get("diagnostics") or []:
            if msg.get("level") == "warning":
                self._summary_text += f"    ⚠ {msg['message']}"
                self.status_var.set(self._summary_text)
                break

        if not products:
            if self._view_mode != "card":
                self._setup_card_view()
            else:
                self._clear_card_rows()
            tk.Label(self.list_frame, text="没有符合条件的产品。",
                     bg="#f3f6fb", fg=COL_MUTED, pady=20).pack()
            return

        if use_tree:
            self._render_tree(products)
        else:
            if self._view_mode != "card":
                self._setup_card_view()
            else:
                self._clear_card_rows()
            self._image_load_budget = min(IMAGE_LOAD_LIMIT, len(products))
            self._render_products_batched(products, self._render_token, 0)

    def _render_products_batched(self, products, render_token, start):
        if render_token != self._render_token:
            return
        self._rendering = True
        end = min(start + CARD_RENDER_BATCH, len(products))
        for item in products[start:end]:
            self._render_row(item, render_token)
        if end < len(products):
            self.root.after(32, lambda: self._render_products_batched(products, render_token, end))
        else:
            self._rendering = False
            self._update_scrollregion()

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
        """一次性填充表格，避免分批插入造成频闪。"""
        self._render_token += 1
        render_token = self._render_token
        need_switch = self._view_mode != "tree"
        if need_switch:
            self._setup_tree_view()
        else:
            self._clear_tree_rows()

        # 隐藏表格，批量插入后再显示，避免中间态闪烁
        if self._tree:
            self._tree.pack_forget()
            if self._tree_vscroll:
                self._tree_vscroll.pack_forget()

        rows = [self._tree_row_values(item) for item in products]

        def fill():
            if render_token != self._render_token or not self._tree:
                return
            for values, tags in rows:
                self._tree.insert("", tk.END, values=values, tags=tags)
            self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            if self._tree_vscroll:
                self._tree_vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.root.after_idle(fill)

    def _render_row(self, item, render_token):
        gap = item["gap"]
        bg = COL_GAP_BG if gap else COL_ROW_BG
        row = tk.Frame(self.list_frame, bg=bg, highlightbackground=COL_BORDER,
                       highlightthickness=1)
        row.pack(fill=tk.X, pady=3, padx=2)

        img_lbl = tk.Label(row, bg=bg)
        raw = item.get("image")
        photo = self._img_cache.get(raw) if raw else None
        if photo is None:
            photo = self._placeholder(item)
            if raw:
                self._schedule_image_load(raw, item, img_lbl, render_token)
        img_lbl.configure(image=photo)
        img_lbl.image = photo
        img_lbl.pack(side=tk.LEFT, padx=8, pady=8)

        info = tk.Frame(row, bg=bg)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=6)
        title = f"{item['code']}  ·  {item['name'] or '（无名称）'}"
        tk.Label(info, text=title, bg=bg, fg=COL_TEXT,
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill=tk.X)
        sub = f"系列：{item['family']}"
        if item["price"] is not None:
            sub += f"    价格：{item['price']:,.2f}"
        tk.Label(info, text=sub, bg=bg, fg=COL_MUTED, anchor="w").pack(fill=tk.X)

        tags = tk.Frame(row, bg=bg)
        tags.pack(side=tk.RIGHT, padx=10)
        if item["in_stock"]:
            self._chip(tags, f"有货 {int(item['stock_qty'])}", COL_OK)
        else:
            self._chip(tags, "无货", COL_MUTED)
        if item["displayed"]:
            self._chip(tags, "已展示", COL_OK)
        else:
            self._chip(tags, "未展示", COL_ERR if gap else COL_MUTED)
        if item["discontinued"]:
            self._chip(tags, "已停产", COL_MUTED)
        if gap:
            self._chip(tags, "★有货未展示", COL_ERR)

    def _chip(self, parent, text, color):
        tk.Label(parent, text=text, fg="#ffffff", bg=color,
                 font=("Segoe UI", 9, "bold"), padx=8, pady=2).pack(side=tk.LEFT, padx=3)

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
