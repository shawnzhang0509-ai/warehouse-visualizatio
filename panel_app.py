"""有货未展示看板 —— 纯本地桌面软件（Tkinter，不走浏览器）。

逐个产品展示：图片、是否有货、在所选店面是否展示；
可切换不同店面（例如 Onehunga），并高亮"有货但未展示"的产品。

运行：
    python panel_app.py          # 或双击 start_panel.bat
数据源见 panel_data.py（默认用 sample_data/ 示例数据）。
"""

import io
import sys
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

        self._img_cache = {}   # path/url -> PhotoImage（防止被回收）
        self._placeholder_cache = {}

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

    # ---------- UI 构建 ----------
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
                label += " (尚无 latest)"
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
        ttk.Button(bar, text="刷新", command=self.reload).pack(side=tk.LEFT)
        ttk.Checkbutton(bar, text="只看有货未展示", variable=self.only_gap_var,
                        command=self.reload).pack(side=tk.LEFT, padx=(12, 0))

        self.summary_lbl = ttk.Label(self.root, textvariable=self.status_var,
                                     padding=(12, 8), font=("Segoe UI", 11))
        self.summary_lbl.pack(fill=tk.X)

        # 滚动区域
        container = ttk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        self.canvas = tk.Canvas(container, highlightthickness=0, background="#f3f6fb")
        vscroll = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.list_frame = tk.Frame(self.canvas, background="#f3f6fb")
        self._win = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>",
                             lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        # 鼠标滚轮
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", lambda _e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind_all("<Button-5>", lambda _e: self.canvas.yview_scroll(1, "units"))

    def _on_wheel(self, event):
        delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")

    def _current_region(self):
        text = self.region_combo.get().strip()
        return text.split(" ")[0] if text else panel_data.SAMPLE_REGION

    def _on_region_change(self):
        region = self._current_region()
        stores = panel_data.list_stores(region)
        self.store_combo.configure(values=stores)
        if self.store_var.get() not in stores:
            self.store_var.set(stores[0] if stores else panel_data.ALL_STORES)
        self.reload()

    # ---------- 图片 ----------
    def _load_thumb(self, item):
        raw = item.get("image")
        if raw and raw in self._img_cache:
            return self._img_cache[raw]
        photo = None
        try:
            if raw:
                if str(raw).lower().startswith(("http://", "https://")):
                    with urllib.request.urlopen(raw, timeout=8) as resp:
                        data = resp.read()
                    if Image is not None:
                        im = Image.open(io.BytesIO(data)); im.thumbnail(THUMB)
                        photo = ImageTk.PhotoImage(im)
                elif Path(raw).exists():
                    if Image is not None:
                        im = Image.open(raw); im.thumbnail(THUMB)
                        photo = ImageTk.PhotoImage(im)
                    else:
                        photo = tk.PhotoImage(file=raw)
        except Exception:
            photo = None
        if photo is not None:
            self._img_cache[raw] = photo
        return photo

    def _placeholder(self, family):
        color = FAMILY_COLORS.get(family, "#64748b")
        if color in self._placeholder_cache:
            return self._placeholder_cache[color]
        img = tk.PhotoImage(width=THUMB[0], height=THUMB[1])
        img.put(color, to=(0, 0, THUMB[0], THUMB[1]))
        self._placeholder_cache[color] = img
        return img

    # ---------- 渲染 ----------
    def reload(self):
        region = self._current_region()
        try:
            data = panel_data.build_products(
                store=self.store_var.get(),
                only_gap=self.only_gap_var.get(),
                region=region,
            )
        except FileNotFoundError as exc:
            self.status_var.set(f"找不到数据文件：{exc}")
            return
        except Exception as exc:
            self.status_var.set(f"加载失败：{exc}")
            return

        s = data["summary"]
        if data["source"] == "sample":
            src = "示例数据"
        elif str(data["source"]).startswith("region-"):
            src = self._region_labels.get(region, region)
        else:
            src = "CSV"
        self.source_var.set(f"数据源：{src}    库存：{data['stock_path']}")

        def pct(v):
            return "-" if v is None else f"{v:.1f}%"
        self.status_var.set(
            f"店面：{s['store']}    "
            f"有货率 {pct(s['in_stock_rate'])}    "
            f"展示覆盖率 {pct(s['display_coverage_rate'])}    "
            f"有货未展示 {s['not_displayed_count']} 个"
            f"（纳入分析 {s['total_non_discontinue']} 个未停产产品）"
        )

        for w in self.list_frame.winfo_children():
            w.destroy()

        if not data["products"]:
            tk.Label(self.list_frame, text="没有符合条件的产品。",
                     bg="#f3f6fb", fg=COL_MUTED, pady=20).pack()
            return

        for item in data["products"]:
            self._render_row(item)

    def _render_row(self, item):
        gap = item["gap"]
        bg = COL_GAP_BG if gap else COL_ROW_BG
        row = tk.Frame(self.list_frame, bg=bg, highlightbackground=COL_BORDER,
                       highlightthickness=1)
        row.pack(fill=tk.X, pady=3, padx=2)

        # 图片
        photo = self._load_thumb(item)
        if photo is None:
            photo = self._placeholder(item["family"])
        img_lbl = tk.Label(row, image=photo, bg=bg)
        img_lbl.image = photo
        img_lbl.pack(side=tk.LEFT, padx=8, pady=8)

        # 文字信息
        info = tk.Frame(row, bg=bg)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=6)
        title = f"{item['code']}  ·  {item['name'] or '（无名称）'}"
        tk.Label(info, text=title, bg=bg, fg=COL_TEXT,
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill=tk.X)
        sub = f"系列：{item['family']}"
        if item["price"] is not None:
            sub += f"    价格：{item['price']:,.2f}"
        tk.Label(info, text=sub, bg=bg, fg=COL_MUTED, anchor="w").pack(fill=tk.X)

        # 状态标签
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
