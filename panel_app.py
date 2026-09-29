"""有货未展示看板 —— 纯本地桌面软件（Tkinter，不走浏览器）。

表格每行内嵌产品缩略图；支持搜索、筛选、排序、按系列分组。

运行：
    python panel_app.py          # 或双击 start_panel.bat
"""

import csv
import io
import os
import ssl
import sys
import threading
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

import panel_data

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except Exception:
    tk = None
    ttk = None
    messagebox = None
    filedialog = None

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

APP_VERSION = "1.9.27"
ROW_HEIGHT = 62
THUMB = (56, 56)
IMAGE_BATCH = 40
ON_HOLD_TREE_BATCH = 80
ON_HOLD_IMAGE_MAX_ROWS = 120
PLACEHOLDER_COLOR = "#d1d5db"
SCROLL_UNITS = 8
AUTO_EXPAND_ALL_GROUPS = 300
MAX_EXPAND_GROUP_ITEMS = 80
LOAD_IMAGES = os.getenv("PANEL_LOAD_IMAGES", "").lower() in ("1", "true", "yes")

C_HEADER = "#1e4f8a"
C_BG = "#f0f4f8"
C_CARD_GAP = "#dc2626"
C_CARD_GAP_BG = "#fee2e2"
C_CARD_GAP_BG_ACTIVE = "#fca5a5"
C_CARD_WAREHOUSE = "#ea580c"
C_CARD_WAREHOUSE_BG = "#ffedd5"
C_CARD_WAREHOUSE_BG_ACTIVE = "#fdba74"
C_CARD_EXEMPT = "#ca8a04"
C_CARD_EXEMPT_BG = "#fef9c3"
C_CARD_EXEMPT_BG_ACTIVE = "#fde047"
C_CARD_OK = "#16a34a"
C_CARD_OK_BG = "#dcfce7"
C_CARD_OK_BG_ACTIVE = "#86efac"
C_CARD_INFO = "#2563eb"
C_CARD_INFO_BG = "#dbeafe"
C_CARD_NEUTRAL = "#64748b"
C_CARD_NEUTRAL_BG = "#f1f5f9"
C_CARD_BORDER_IDLE = "#94a3b8"
C_CARD_DISABLED_BG = "#e2e8f0"
C_CARD_DISABLED_FG = "#94a3b8"
C_ROW_GAP = "#fff1f2"
C_ROW_EXEMPT = "#fffbeb"
C_ROW_WAREHOUSE = "#ffedd5"
C_ROW_OK = "#f0fdf4"
C_ROW_ALT = "#fafbfc"
C_ROW_DISC = "#fee2e2"
C_TEXT = "#1e293b"
C_MUTED = "#64748b"
ISLAND_QUADRANT_STYLE = {
    "both": (C_CARD_OK_BG, C_CARD_OK_BG_ACTIVE, C_CARD_OK),
    "south_only": (C_CARD_WAREHOUSE_BG, C_CARD_WAREHOUSE_BG_ACTIVE, C_CARD_WAREHOUSE),
    "north_only": (C_CARD_INFO_BG, C_CARD_INFO_BG, C_CARD_INFO),
    "none": (C_CARD_NEUTRAL_BG, C_CARD_NEUTRAL_BG, C_CARD_NEUTRAL),
}

SORTABLE_COLS = {
    "code": lambda p: (p.get("code") or "").lower(),
    "name": lambda p: (p.get("name") or "").lower(),
    "family": lambda p: (p.get("family") or "").lower(),
    "price": lambda p: p.get("price") if p.get("price") is not None else -1,
    "stock": lambda p: float(p.get("stock_qty") or 0),
    "display": lambda p: 0 if p.get("displayed") else 1,
    "discontinue": lambda p: 0 if p.get("discontinued") else 1,
    "island": lambda p: (
        0 if p.get("island_stock_class") == "both" else
        1 if p.get("island_stock_class") == "south_only" else
        2 if p.get("island_stock_class") == "north_only" else
        3 if p.get("island_stock_class") == "none" else 9
    ),
    "status": lambda p: (
        0 if p.get("ready_not_displayed") else
        1 if p.get("warehouse_only") and not p.get("exempted") else
        2 if p.get("gap") else 3 if p.get("exempted") else 4 if p.get("in_stock") else 5
    ),
}

ONHOLD_SORTABLE_COLS = {
    "code": lambda r: (r.get("code") or "").lower(),
    "name": lambda r: (r.get("name") or "").lower(),
    "status": lambda r: (r.get("status") or "").lower(),
    "order_no": lambda r: (r.get("order_no") or "").lower(),
    "ticket_no": lambda r: (r.get("ticket_no") or "").lower(),
    "hold_days": lambda r: (
        r.get("hold_days") if r.get("hold_days") is not None else -1
    ),
    "hold_since": lambda r: (r.get("hold_since") or ""),
    "qty": lambda r: float(r.get("qty") or 0),
    "warehouse": lambda r: (r.get("warehouse") or "").lower(),
}
ONHOLD_NUMERIC_SORT_COLS = frozenset({"qty", "hold_days"})


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
        self._pending_urls = {}
        self._loading_urls = set()
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
        self._owner_rendered_for = None
        self._island_rendered_for = None
        self._island_quadrant_cards = {}
        self._island_quadrant_pcts = {}
        self._island_quadrant_meta = {}
        self._island_selected_class = None
        self._mining_tree = None
        self._mining_vscroll = None
        self._mining_transfer_tree = None
        self._mining_transfer_vscroll = None
        self._mining_onhold_tree = None
        self._mining_onhold_vscroll = None
        self._onhold_summary_frame = None
        self._onhold_status_combo = None
        self._mining_onhold_render_token = 0
        self._mining_onhold_row_data = {}
        self._onhold_sort_col = None
        self._onhold_sort_reverse = False
        self._tab_onhold = None
        self._tab_transfer = None
        self._onhold_status_lbl = None
        self._transfer_status_lbl = None
        self._mining_rendered_for = None
        self._island_groups_canvas = None
        self._island_groups_frame = None
        self._island_single_frame = None
        self._cached_owner_config = []
        self._cached_owner_path = ""
        self._lazy_groups = {}
        self._group_labels = {}
        self._loaded_full_stock = False
        self._prewarm_token = 0
        self._sort_col = None
        self._sort_reverse = False

        self._tree = None
        self._tree_vscroll = None
        self._prefix_tree = None
        self._prefix_vscroll = None
        self._owner_tree = None
        self._owner_vscroll = None
        self._owner_channel_tree = None
        self._owner_channel_vscroll = None
        self._owner_paned = None
        self._owner_summary_frame = None
        self._owner_detail_frame = None
        self._cached_owner_report = None
        self._owner_detail_filter = None
        self._owner_filter_var = tk.StringVar(value="全部负责人")
        self._owner_view_var = tk.StringVar(value="分栏视图")
        self._notebook = None
        self._tab_products = None
        self._placeholder_photo = None
        self._stat_labels = {}
        self._stat_hints = {}
        self._stat_cards = {}
        self._stat_card_meta = {}
        self._quick_filter = None

        try:
            from runner_config import ensure_runner_config
            ensure_runner_config()
        except Exception:
            pass
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
        self.only_warehouse_only_var = tk.BooleanVar(value=False)
        self.only_exempted_var = tk.BooleanVar(value=False)
        self.source_var = tk.StringVar(value="")
        self.search_var = tk.StringVar()
        self.stock_filter_var = tk.StringVar(value="全部")
        self.display_filter_var = tk.StringVar(value="全部")
        self.discontinue_filter_var = tk.StringVar(value="在产")
        self.group_sort_var = tk.StringVar(value="库存总数多到少")
        self.island_filter_var = tk.StringVar(value="全部")
        self._island_view_mode_var = tk.StringVar(value="总览")
        self._island_owner_filter_var = tk.StringVar(value="全部负责人")
        self._island_channel_filter_var = tk.StringVar(value="全部渠道")
        self._mining_kind_var = tk.StringVar(value="全部")
        self._onhold_status_filter_var = tk.StringVar(value="全部状态")
        self._onhold_days_filter_var = tk.StringVar(value="全部天数")
        self._onhold_days_combo = None
        self._onhold_sort_col = None
        self._onhold_sort_reverse = False
        self._filter_combos = []
        self.load_images_var = tk.BooleanVar(value=True)
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
        self.region_combo = ttk.Combobox(toolbar, width=22, state="readonly", values=region_values)
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
        self.view_img_btn.grid(row=1, column=3, sticky="w", padx=(0, 8), pady=(2, 0))
        self._expand_all_btn = ttk.Button(
            toolbar, text="展开全部系列", style="Tool.TButton",
            command=self._expand_all_groups, state=tk.DISABLED,
        )
        self._expand_all_btn.grid(row=1, column=4, sticky="w", padx=(0, 6), pady=(2, 0))
        self._collapse_all_btn = ttk.Button(
            toolbar, text="折叠全部系列", style="Tool.TButton",
            command=self._collapse_all_groups, state=tk.DISABLED,
        )
        self._collapse_all_btn.grid(row=1, column=5, sticky="w", padx=(0, 8), pady=(2, 0))
        self._export_btn = ttk.Button(
            toolbar, text="导出当前筛选", style="Tool.TButton",
            command=self._export_current_filtered,
        )
        self._export_btn.grid(row=1, column=6, sticky="w", pady=(2, 0))

        filter_bar = tk.Frame(self.root, bg="white", padx=14, pady=8)
        filter_bar.pack(fill=tk.X, padx=12, pady=(6, 0))

        tk.Label(filter_bar, text="搜索", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        search_entry = ttk.Entry(filter_bar, textvariable=self.search_var, width=28)
        search_entry.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(2, 0))

        filters = [
            ("库存", self.stock_filter_var, ("全部", "有货", "无货"), 1),
            ("展示", self.display_filter_var, ("全部", "已展示", "未展示"), 2),
            ("停产", self.discontinue_filter_var, ("在产", "全部", "已停产"), 3),
            ("南北岛", self.island_filter_var,
             ("全部", "南北都有", "南有北无", "北有南无", "南北都没"), 4),
            ("组排序", self.group_sort_var, ("字母序", "SKU数量多到少", "库存总数多到少"), 5),
        ]
        for label, var, values, col in filters:
            tk.Label(filter_bar, text=label, bg="white", fg=C_MUTED, font=("Segoe UI", 9)).grid(
                row=0, column=col, sticky="w")
            cb = ttk.Combobox(filter_bar, width=10, state="readonly", textvariable=var, values=values)
            cb.grid(row=1, column=col, sticky="w", padx=(0, 10), pady=(2, 0))
            self._filter_combos.append((cb, var))
            cb.bind("<<ComboboxSelected>>", lambda _e: self.root.after_idle(self._on_filter_combo_change))

        ttk.Checkbutton(filter_bar, text="行内缩略图", variable=self.load_images_var,
                        command=self._on_toggle_inline_images).grid(row=1, column=6, sticky="w", padx=(4, 0))

        tk.Label(filter_bar, textvariable=self.result_count_var, bg="white", fg=C_MUTED,
                 font=("Segoe UI", 9)).grid(row=1, column=7, sticky="e", padx=(12, 0))
        tk.Label(filter_bar, textvariable=self._status_var, bg="white", fg=C_CARD_GAP,
                 font=("Segoe UI", 9)).grid(row=0, column=7, sticky="e", padx=(12, 0))
        filter_bar.columnconfigure(7, weight=1)

        cards = tk.Frame(self.root, bg=C_BG, padx=12, pady=8)
        cards.pack(fill=tk.X)
        card_defs = [
            ("gap", "有货未展示", "0", C_CARD_GAP_BG, C_CARD_GAP_BG_ACTIVE, C_CARD_GAP, True,
             "含在产与停产 · 点击筛选"),
            ("warehouse_only", "仓有·店仓无", "0", C_CARD_WAREHOUSE_BG, C_CARD_WAREHOUSE_BG_ACTIVE,
             C_CARD_WAREHOUSE, True, "中心仓有货、店后仓无 · 点击筛选"),
            ("exempted", "同组豁免", "0", C_CARD_EXEMPT_BG, C_CARD_EXEMPT_BG_ACTIVE, C_CARD_EXEMPT, True,
             "同系列已陈列 · 点击筛选"),
            ("in_stock", "有货产品", "0", C_CARD_OK_BG, C_CARD_OK_BG_ACTIVE, C_CARD_OK, True,
             "切换有货/无货 · 点击筛选"),
            ("rate", "有货率", "-", C_CARD_INFO_BG, C_CARD_INFO_BG, C_CARD_INFO, False, ""),
            ("total", "纳入分析", "0", C_CARD_NEUTRAL_BG, C_CARD_NEUTRAL_BG, C_CARD_NEUTRAL, False, ""),
        ]
        for i, (key, title, val, bg, bg_active, fg, filterable, hint_idle) in enumerate(card_defs):
            card = tk.Frame(cards, bg=bg, padx=16, pady=10, cursor="hand2" if filterable else "arrow",
                            highlightthickness=2, highlightbackground=C_CARD_BORDER_IDLE)
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0 if i == 0 else 6, 0))
            title_lbl = tk.Label(
                card, text=title, bg=bg, fg=fg,
                font=("Segoe UI", 9, "bold" if filterable else "normal"),
                cursor="hand2" if filterable else "arrow",
            )
            title_lbl.pack(anchor="w")
            val_lbl = tk.Label(
                card, text=val, bg=bg, fg=fg, font=("Segoe UI", 18, "bold"),
                cursor="hand2" if filterable else "arrow",
            )
            val_lbl.pack(anchor="w", pady=(2, 0))
            hint = None
            if filterable:
                hint = tk.Label(
                    card, text=hint_idle, bg=bg, fg=fg, font=("Segoe UI", 8),
                    cursor="hand2",
                )
                hint.pack(anchor="w")
                self._stat_hints[key] = hint
                for w in (card, title_lbl, val_lbl, hint):
                    w.bind("<Button-1>", lambda _e, k=key: self._on_stat_card_click(k))
            self._stat_labels[key] = val_lbl
            self._stat_cards[key] = card
            self._stat_card_meta[key] = {
                "card": card,
                "widgets": [title_lbl, val_lbl] + ([hint] if hint else []),
                "bg": bg,
                "bg_active": bg_active,
                "fg": fg,
                "filterable": filterable,
                "hint_idle": hint_idle,
                "hint": hint,
            }

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

        tree_body = tk.Frame(inner, bg="white")
        tree_body.pack(fill=tk.BOTH, expand=True)
        tree_body.grid_rowconfigure(0, weight=1)
        tree_body.grid_columnconfigure(0, weight=1)

        columns = ("code", "name", "family", "price", "stock", "island", "display", "discontinue", "status")
        self._tree = ttk.Treeview(tree_body, columns=columns, show="tree headings", selectmode="browse")
        self._tree.heading("#0", text="产品图")
        self._tree.column("#0", width=72, minwidth=68, stretch=False, anchor="center")
        headings = {
            "code": ("编码", 104), "name": ("名称", 260), "family": ("系列", 88),
            "price": ("价格", 72), "stock": ("库存", 100), "island": ("南北岛", 76),
            "display": ("展示", 58), "discontinue": ("停产", 52), "status": ("状态", 128),
        }
        for col, (text, width) in headings.items():
            self._tree.heading(col, text=text, command=lambda c=col: self._on_sort_column(c))
            anchor = "w" if col in ("code", "name", "family") else "center"
            stretch = col == "name"
            min_w = 72 if col == "name" else width
            self._tree.column(col, width=width, minwidth=min_w, anchor=anchor, stretch=stretch)

        for tag, bg in (("gap", C_ROW_GAP), ("warehouse_only", C_ROW_WAREHOUSE),
                        ("exempted", C_ROW_EXEMPT), ("ok", C_ROW_OK),
                        ("alt", C_ROW_ALT), ("discontinued", C_ROW_DISC), ("group", "#e2e8f0")):
            self._tree.tag_configure(tag, background=bg)
        self._tree.tag_configure("group", font=("Segoe UI", 10, "bold"))

        self._tree_vscroll = ttk.Scrollbar(tree_body, orient="vertical", command=self._on_tree_yscroll)
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
            text="按 SKU 编码前三位汇总（在产 SKU 数量）· 默认按有货率降序 · 行色=有货率（绿高/红低）· 双击筛选前缀",
            bg="white", fg=C_MUTED, font=("Segoe UI", 9),
        ).pack(anchor="w", padx=4, pady=(0, 6))

        pcols = ("prefix", "owner", "total", "in_stock", "in_stock_rate", "displayed",
                 "display_rate", "gap", "exempted")
        self._prefix_tree = ttk.Treeview(
            prefix_inner, columns=pcols, show="headings",
            selectmode="browse", style="Prefix.Treeview",
        )
        pheads = {
            "prefix": ("SKU前缀", 72), "owner": ("负责人", 72), "total": ("在产SKU数", 80),
            "in_stock": ("有货数", 64), "in_stock_rate": ("有货率", 72), "displayed": ("有货已展示", 88),
            "display_rate": ("展示覆盖率", 88), "gap": ("有货未展示", 88), "exempted": ("同组豁免", 72),
        }
        for col, (text, width) in pheads.items():
            self._prefix_tree.heading(col, text=text)
            self._prefix_tree.column(col, width=width, anchor="center" if col != "prefix" else "w")
        self._prefix_tree.tag_configure("ok", background=C_ROW_OK)
        self._prefix_tree.tag_configure("warn", background="#fff7ed")
        self._prefix_tree.tag_configure("low", background=C_ROW_GAP)
        self._prefix_tree.tag_configure("alt", background=C_ROW_ALT)

        self._prefix_vscroll = ttk.Scrollbar(prefix_inner, orient="vertical", command=self._on_prefix_yscroll)
        self._prefix_tree.configure(yscrollcommand=self._prefix_vscroll.set)
        self._prefix_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._prefix_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._prefix_tree.bind("<Double-1>", self._on_prefix_double_click)

        # ── 负责人报表 ──
        self._tab_owner = ttk.Frame(self._notebook)
        self._notebook.add(self._tab_owner, text="负责人报表")
        self._notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_change)
        owner_inner = tk.Frame(self._tab_owner, bg="white")
        owner_inner.pack(fill=tk.BOTH, expand=True)
        owner_toolbar = tk.Frame(owner_inner, bg="white")
        owner_toolbar.pack(fill=tk.X, padx=4, pady=(0, 6))
        tk.Label(
            owner_inner,
            text="按 channel_owners.csv 汇总各负责人渠道有货率 · 支持合并计算产品组 · 可导出定期报表",
            bg="white", fg=C_MUTED, font=("Segoe UI", 9),
        ).pack(anchor="w", padx=4, pady=(0, 4))
        tk.Label(owner_toolbar, text="负责人", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).pack(
            side=tk.LEFT,
        )
        self._owner_filter_combo = ttk.Combobox(
            owner_toolbar, width=14, state="readonly", textvariable=self._owner_filter_var,
            values=["全部负责人"],
        )
        self._owner_filter_combo.pack(side=tk.LEFT, padx=(6, 12))
        self._owner_filter_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_owner_filter_change())
        tk.Label(owner_toolbar, text="视图", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._owner_view_combo = ttk.Combobox(
            owner_toolbar, width=12, state="readonly", textvariable=self._owner_view_var,
            values=["分栏视图", "负责人汇总", "渠道明细"],
        )
        self._owner_view_combo.pack(side=tk.LEFT, padx=(6, 12))
        self._owner_view_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_owner_view_change())
        ttk.Button(
            owner_toolbar, text="导出 CSV 报表", style="Tool.TButton",
            command=self._export_owner_report,
        ).pack(side=tk.LEFT)
        self._owner_status_lbl = tk.Label(
            owner_toolbar, text="", bg="white", fg=C_MUTED, font=("Segoe UI", 9),
        )
        self._owner_status_lbl.pack(side=tk.RIGHT, padx=(8, 0))

        self._owner_paned = tk.PanedWindow(owner_inner, orient=tk.VERTICAL, sashwidth=4, bg="white")
        self._owner_paned.pack(fill=tk.BOTH, expand=True)
        self._owner_summary_frame = tk.Frame(self._owner_paned, bg="white")
        self._owner_detail_frame = tk.Frame(self._owner_paned, bg="white")
        self._owner_paned.add(self._owner_summary_frame, minsize=120)
        self._owner_paned.add(self._owner_detail_frame, minsize=160)

        tk.Label(
            self._owner_summary_frame, text="负责人汇总", bg="white", fg=C_MUTED,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=4, pady=(0, 2))
        owner_summary_wrap = tk.Frame(self._owner_summary_frame, bg="white")
        owner_summary_wrap.pack(fill=tk.BOTH, expand=True)
        ocols = (
            "owner", "channel", "lead_time", "merge", "sku", "units", "in_stock",
            "in_stock_rate", "gap", "exempted", "note",
        )
        self._owner_tree = ttk.Treeview(
            owner_summary_wrap, columns=ocols, show="headings",
            selectmode="browse", style="Prefix.Treeview", height=6,
        )
        self._owner_tree.tag_configure("ok", background=C_ROW_OK)
        self._owner_tree.tag_configure("warn", background="#fff7ed")
        self._owner_tree.tag_configure("low", background=C_ROW_GAP)
        self._owner_tree.tag_configure("alt", background=C_ROW_ALT)
        self._owner_vscroll = ttk.Scrollbar(owner_summary_wrap, orient="vertical", command=self._owner_tree.yview)
        self._owner_tree.configure(yscrollcommand=self._owner_vscroll.set)
        self._owner_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._owner_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._owner_tree.bind("<<TreeviewSelect>>", self._on_owner_select)
        self._owner_tree.bind("<Double-1>", self._on_owner_summary_double_click)

        tk.Label(
            self._owner_detail_frame, text="渠道明细（点击上方负责人可筛选）", bg="white", fg=C_MUTED,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=4, pady=(6, 2))
        owner_detail_wrap = tk.Frame(self._owner_detail_frame, bg="white")
        owner_detail_wrap.pack(fill=tk.BOTH, expand=True)
        self._owner_channel_tree = ttk.Treeview(
            owner_detail_wrap, columns=ocols, show="headings",
            selectmode="browse", style="Prefix.Treeview",
        )
        self._owner_channel_tree.tag_configure("ok", background=C_ROW_OK)
        self._owner_channel_tree.tag_configure("warn", background="#fff7ed")
        self._owner_channel_tree.tag_configure("low", background=C_ROW_GAP)
        self._owner_channel_tree.tag_configure("alt", background=C_ROW_ALT)
        self._owner_channel_vscroll = ttk.Scrollbar(
            owner_detail_wrap, orient="vertical", command=self._owner_channel_tree.yview,
        )
        self._owner_channel_tree.configure(yscrollcommand=self._owner_channel_vscroll.set)
        self._owner_channel_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._owner_channel_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._owner_channel_tree.bind("<Double-1>", self._on_owner_channel_double_click)
        for widget in (
            owner_inner, self._tab_owner, self._owner_tree, self._owner_channel_tree,
            self._owner_summary_frame, self._owner_detail_frame,
        ):
            widget.bind("<MouseWheel>", self._on_owner_wheel)
            widget.bind("<Button-4>", lambda _e: self._scroll_owner(-1))
            widget.bind("<Button-5>", lambda _e: self._scroll_owner(1))

        # ── 南北岛象限 ──
        self._tab_island = ttk.Frame(self._notebook)
        self._notebook.add(self._tab_island, text="南北岛象限")
        island_inner = tk.Frame(self._tab_island, bg="white")
        island_inner.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            island_inner,
            text="按全国仓库存划分：北岛=Carbine+Walls，南岛=GC · 先选负责人，再选渠道（SKU 前三位，如 130、830）",
            bg="white", fg=C_MUTED, font=("Segoe UI", 9),
        ).pack(anchor="w", padx=8, pady=(6, 4))
        island_filter_bar = tk.Frame(island_inner, bg="white")
        island_filter_bar.pack(fill=tk.X, padx=8, pady=(0, 6))
        tk.Label(island_filter_bar, text="视图", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).pack(
            side=tk.LEFT,
        )
        self._island_view_combo = ttk.Combobox(
            island_filter_bar, width=10, state="readonly", textvariable=self._island_view_mode_var,
            values=["总览", "按负责人", "按渠道"],
        )
        self._island_view_combo.pack(side=tk.LEFT, padx=(6, 12))
        self._island_view_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_island_scope_change())
        tk.Label(island_filter_bar, text="负责人", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).pack(
            side=tk.LEFT,
        )
        self._island_owner_combo = ttk.Combobox(
            island_filter_bar, width=12, state="readonly", textvariable=self._island_owner_filter_var,
            values=["全部负责人"],
        )
        self._island_owner_combo.pack(side=tk.LEFT, padx=(6, 12))
        self._island_owner_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_island_owner_change())
        tk.Label(island_filter_bar, text="渠道", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).pack(
            side=tk.LEFT,
        )
        self._island_channel_combo = ttk.Combobox(
            island_filter_bar, width=12, state="disabled", textvariable=self._island_channel_filter_var,
            values=["全部渠道"],
        )
        self._island_channel_combo.pack(side=tk.LEFT, padx=(6, 12))
        self._island_channel_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_island_channel_change())
        self._island_channel_combo.bind(
            "<FocusOut>", lambda _e: self.root.after_idle(self._on_island_channel_change),
        )
        self._island_unsupported_lbl = tk.Label(
            island_inner, text="", bg="white", fg=C_CARD_GAP, font=("Segoe UI", 10),
        )
        self._island_unsupported_lbl.pack(anchor="w", padx=8, pady=(0, 4))
        self._island_single_frame = tk.Frame(island_inner, bg="white")
        self._island_single_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        quadrant_wrap = tk.Frame(self._island_single_frame, bg="white")
        quadrant_wrap.pack(fill=tk.X)
        groups_outer = tk.Frame(island_inner, bg="white")
        groups_outer.pack(fill=tk.BOTH, expand=False, padx=8, pady=(0, 8))
        self._island_groups_canvas = tk.Canvas(groups_outer, bg="white", height=220, highlightthickness=0)
        groups_scroll = ttk.Scrollbar(groups_outer, orient="vertical", command=self._island_groups_canvas.yview)
        self._island_groups_frame = tk.Frame(self._island_groups_canvas, bg="white")
        self._island_groups_window = self._island_groups_canvas.create_window(
            (0, 0), window=self._island_groups_frame, anchor="nw",
        )
        self._island_groups_frame.bind(
            "<Configure>",
            lambda _e: self._island_groups_canvas.configure(
                scrollregion=self._island_groups_canvas.bbox("all"),
            ),
        )
        self._island_groups_canvas.configure(yscrollcommand=groups_scroll.set)
        self._island_groups_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        groups_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        groups_outer.pack_forget()
        self._island_groups_outer = groups_outer
        tk.Label(quadrant_wrap, text="", bg="white", width=8).grid(row=1, column=0)
        tk.Label(
            quadrant_wrap, text="北岛有货", bg="white", fg=C_MUTED,
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=1, padx=6, pady=(0, 4))
        tk.Label(
            quadrant_wrap, text="北岛无货", bg="white", fg=C_MUTED,
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=2, padx=6, pady=(0, 4))
        tk.Label(
            quadrant_wrap, text="南岛有货", bg="white", fg=C_MUTED,
            font=("Segoe UI", 9, "bold"), anchor="e",
        ).grid(row=1, column=0, padx=(0, 6), sticky="e")
        tk.Label(
            quadrant_wrap, text="南岛无货", bg="white", fg=C_MUTED,
            font=("Segoe UI", 9, "bold"), anchor="e",
        ).grid(row=2, column=0, padx=(0, 6), sticky="e")
        for row_idx, row_keys in enumerate(panel_data.ISLAND_STOCK_GRID):
            for col_idx, class_key in enumerate(row_keys):
                bg, bg_active, fg = ISLAND_QUADRANT_STYLE[class_key]
                card = tk.Frame(
                    quadrant_wrap, bg=bg, padx=18, pady=14, cursor="hand2",
                    highlightthickness=2, highlightbackground=C_CARD_BORDER_IDLE,
                )
                card.grid(row=row_idx + 1, column=col_idx + 1, padx=6, pady=6, sticky="nsew")
                title_lbl = tk.Label(
                    card, text=panel_data.ISLAND_STOCK_CLASSES[class_key], bg=bg, fg=fg,
                    font=("Segoe UI", 10, "bold"), cursor="hand2",
                )
                title_lbl.pack(anchor="w")
                val_lbl = tk.Label(
                    card, text="0", bg=bg, fg=fg, font=("Segoe UI", 22, "bold"), cursor="hand2",
                )
                val_lbl.pack(anchor="w", pady=(4, 0))
                pct_lbl = tk.Label(
                    card, text="0.0%", bg=bg, fg=fg, font=("Segoe UI", 10), cursor="hand2",
                )
                pct_lbl.pack(anchor="w")
                hint_lbl = tk.Label(
                    card, text="点击筛选", bg=bg, fg=fg, font=("Segoe UI", 8), cursor="hand2",
                )
                hint_lbl.pack(anchor="w")
                for widget in (card, title_lbl, val_lbl, pct_lbl, hint_lbl):
                    widget.bind(
                        "<Button-1>",
                        lambda _e, key=class_key: self._on_island_quadrant_click(key),
                    )
                self._island_quadrant_cards[class_key] = val_lbl
                self._island_quadrant_pcts[class_key] = pct_lbl
                self._island_quadrant_meta[class_key] = {
                    "card": card, "widgets": [title_lbl, val_lbl, pct_lbl, hint_lbl],
                    "bg": bg, "bg_active": bg_active, "fg": fg, "hint": hint_lbl,
                }
        for col in (1, 2):
            quadrant_wrap.grid_columnconfigure(col, weight=1)
        island_toolbar = tk.Frame(island_inner, bg="white")
        island_toolbar.pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Button(
            island_toolbar, text="清除象限筛选", style="Tool.TButton",
            command=self._clear_island_quadrant_filter,
        ).pack(side=tk.LEFT)
        ttk.Button(
            island_toolbar, text="在产品明细中查看", style="Tool.TButton",
            command=self._open_island_selection_in_products,
        ).pack(side=tk.LEFT, padx=(8, 0))
        self._island_status_lbl = tk.Label(
            island_toolbar, text="", bg="white", fg=C_MUTED, font=("Segoe UI", 9),
        )
        self._island_status_lbl.pack(side=tk.RIGHT)
        island_table_wrap = tk.Frame(island_inner, bg="white")
        island_table_wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        icols = ("code", "name", "owner", "channel", "north", "south", "island", "stock", "status")
        self._island_tree = ttk.Treeview(
            island_table_wrap, columns=icols, show="headings",
            selectmode="browse", style="Prefix.Treeview",
        )
        island_headings = {
            "code": ("编码", 96), "name": ("名称", 220), "owner": ("负责人", 72),
            "channel": ("渠道", 64), "north": ("北岛", 52), "south": ("南岛", 52),
            "island": ("象限", 72), "stock": ("店面库存", 72), "status": ("状态", 100),
        }
        for col, (text, width) in island_headings.items():
            self._island_tree.heading(col, text=text)
            self._island_tree.column(col, width=width, anchor="center" if col != "name" else "w")
        self._island_tree.tag_configure("ok", background=C_ROW_OK)
        self._island_tree.tag_configure("warn", background="#fff7ed")
        self._island_tree.tag_configure("low", background=C_ROW_GAP)
        self._island_tree.tag_configure("alt", background=C_ROW_ALT)
        self._island_vscroll = ttk.Scrollbar(
            island_table_wrap, orient="vertical", command=self._island_tree.yview,
        )
        self._island_tree.configure(yscrollcommand=self._island_vscroll.set)
        self._island_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._island_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._island_tree.bind("<Double-1>", self._on_island_double_click)
        for widget in (island_inner, self._tab_island, self._island_tree, self._island_groups_canvas):
            widget.bind("<MouseWheel>", self._on_island_wheel)
            widget.bind("<Button-4>", lambda _e: self._scroll_island(-1))
            widget.bind("<Button-5>", lambda _e: self._scroll_island(1))

        # ── 配件库存 ──
        self._tab_mining = ttk.Frame(self._notebook)
        self._notebook.add(self._tab_mining, text="配件库存")
        mining_inner = tk.Frame(self._tab_mining, bg="white")
        mining_inner.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            mining_inner,
            text="Output 目录 parts.csv · On Hold 见「On Hold 分析」· 跨仓借调见「跨仓借调」",
            bg="white", fg=C_MUTED, font=("Segoe UI", 9),
        ).pack(anchor="w", padx=8, pady=(6, 4))
        self._mining_status_lbl = tk.Label(
            mining_inner, text="", bg="white", fg=C_MUTED, font=("Segoe UI", 9),
        )
        self._mining_status_lbl.pack(anchor="e", padx=8, pady=(0, 4))
        mining_list_tab = mining_inner
        mining_toolbar = tk.Frame(mining_list_tab, bg="white")
        mining_toolbar.pack(fill=tk.X, pady=(6, 6))
        tk.Label(mining_toolbar, text="类型", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._mining_kind_combo = ttk.Combobox(
            mining_toolbar, width=12, state="readonly", textvariable=self._mining_kind_var,
            values=["全部", "On Hold", "配件"],
        )
        self._mining_kind_combo.pack(side=tk.LEFT, padx=(6, 12))
        self._mining_kind_combo.bind("<<ComboboxSelected>>", lambda _e: self._render_mining_table())
        mining_table_wrap = tk.Frame(mining_list_tab, bg="white")
        mining_table_wrap.pack(fill=tk.BOTH, expand=True)
        mcols = ("kind", "code", "name", "family", "qty", "detail", "warehouses")
        self._mining_tree = ttk.Treeview(
            mining_table_wrap, columns=mcols, show="headings",
            selectmode="browse", style="Prefix.Treeview",
        )
        mining_headings = {
            "kind": ("类型", 72), "code": ("编码", 104), "name": ("名称", 240),
            "family": ("系列", 100), "qty": ("数量", 64), "detail": ("状态/备注", 120),
            "warehouses": ("仓", 200),
        }
        for col, (text, width) in mining_headings.items():
            self._mining_tree.heading(col, text=text)
            self._mining_tree.column(
                col, width=width, anchor="center" if col not in ("name", "warehouses") else "w",
            )
        self._mining_tree.tag_configure("hold", background="#fef3c7")
        self._mining_tree.tag_configure("part", background="#e0f2fe")
        self._mining_tree.tag_configure("alt", background=C_ROW_ALT)
        self._mining_vscroll = ttk.Scrollbar(
            mining_table_wrap, orient="vertical", command=self._mining_tree.yview,
        )
        self._mining_tree.configure(yscrollcommand=self._mining_vscroll.set)
        self._mining_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._mining_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._mining_tree.bind("<Double-1>", self._on_mining_double_click)

        # ── On Hold 分析（顶级页）──
        self._tab_onhold = ttk.Frame(self._notebook)
        self._notebook.add(self._tab_onhold, text="On Hold 分析")
        self._mining_onhold_tab = self._tab_onhold
        onhold_tab = self._tab_onhold
        tk.Label(
            onhold_tab,
            text="每行 CSV 一条记录（同 SKU 不同订单/工单/时间不合并）；需导出 OrderNo、TicketNo、OnHoldDate、WarehouseName 等列",
            bg="white", fg=C_MUTED, font=("Segoe UI", 9), wraplength=920, justify="left",
        ).pack(anchor="w", padx=4, pady=(6, 4))
        self._onhold_summary_frame = tk.Frame(onhold_tab, bg="white")
        self._onhold_summary_frame.pack(fill=tk.X, padx=4, pady=(0, 6))
        onhold_toolbar = tk.Frame(onhold_tab, bg="white")
        onhold_toolbar.pack(fill=tk.X, padx=4, pady=(0, 6))
        tk.Label(onhold_toolbar, text="状态", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._onhold_status_combo = ttk.Combobox(
            onhold_toolbar, width=28, state="readonly", textvariable=self._onhold_status_filter_var,
            values=["全部状态"],
        )
        self._onhold_status_combo.pack(side=tk.LEFT, padx=(6, 12))
        self._onhold_status_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_onhold_status_change())
        tk.Label(onhold_toolbar, text="冻结天数", bg="white", fg=C_MUTED, font=("Segoe UI", 9)).pack(
            side=tk.LEFT, padx=(4, 0),
        )
        self._onhold_days_combo = ttk.Combobox(
            onhold_toolbar, width=12, state="readonly", textvariable=self._onhold_days_filter_var,
            values=["全部天数", "30天以上", "90天以上", "360天以上"],
        )
        self._onhold_days_combo.pack(side=tk.LEFT, padx=(6, 12))
        self._onhold_days_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_onhold_status_change())
        ttk.Button(
            onhold_toolbar, text="查看图片", style="Tool.TButton",
            command=self._open_onhold_selected_image,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            onhold_toolbar, text="导出当前筛选", style="Tool.TButton",
            command=self._export_filtered_onhold,
        ).pack(side=tk.LEFT, padx=(0, 8))
        self._onhold_status_lbl = tk.Label(
            onhold_tab, text="", bg="white", fg=C_MUTED, font=("Segoe UI", 9),
        )
        self._onhold_status_lbl.pack(anchor="e", padx=8, pady=(0, 4))
        onhold_wrap = tk.Frame(onhold_tab, bg="white")
        onhold_wrap.pack(fill=tk.BOTH, expand=True)
        ohcols = ("code", "name", "status", "order_no", "ticket_no", "hold_days", "hold_since", "qty", "warehouse")
        self._mining_onhold_tree = ttk.Treeview(
            onhold_wrap, columns=ohcols, show="tree headings", selectmode="browse",
        )
        self._mining_onhold_tree.heading("#0", text="图")
        self._mining_onhold_tree.column("#0", width=56, minwidth=52, stretch=False, anchor="center")
        onhold_headings = {
            "code": ("编码", 92), "name": ("名称", 180), "status": ("On Hold 类型", 128),
            "order_no": ("订单号", 88), "ticket_no": ("Ticket", 72),
            "hold_days": ("冻结天数", 64), "hold_since": ("起始日", 84), "qty": ("数量", 52),
            "warehouse": ("仓", 120),
        }
        for col, (text, width) in onhold_headings.items():
            self._mining_onhold_tree.heading(
                col, text=text, command=lambda c=col: self._on_onhold_sort_column(c),
            )
            self._mining_onhold_tree.column(
                col, width=width, anchor="center" if col not in ("name", "warehouse", "status") else "w",
            )
        self._mining_onhold_tree.tag_configure("hold", background="#fef3c7")
        self._mining_onhold_tree.tag_configure("alt", background=C_ROW_ALT)
        self._mining_onhold_vscroll = ttk.Scrollbar(
            onhold_wrap, orient="vertical", command=self._mining_onhold_tree.yview,
        )
        self._mining_onhold_tree.configure(yscrollcommand=self._mining_onhold_vscroll.set)
        self._mining_onhold_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._mining_onhold_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._mining_onhold_tree.bind("<Double-1>", self._on_mining_onhold_double_click)

        # ── 跨仓借调（顶级页）──
        self._tab_transfer = ttk.Frame(self._notebook)
        self._notebook.add(self._tab_transfer, text="跨仓借调")
        self._mining_transfer_tab = self._tab_transfer
        mining_transfer_tab = self._tab_transfer
        tk.Label(
            mining_transfer_tab,
            text="借调策略：先在 Carbine↔Walls 北岛互调凑套；仍不足再用 CHCH 南岛件补北岛。现有合计=三仓各自成套之和；北岛调后=仅 C+W 合并；调货后=三仓合并。",
            bg="white", fg=C_MUTED, font=("Segoe UI", 9), wraplength=900, justify="left",
        ).pack(anchor="w", padx=4, pady=(6, 4))
        self._transfer_status_lbl = tk.Label(
            mining_transfer_tab, text="", bg="white", fg=C_MUTED, font=("Segoe UI", 9),
        )
        self._transfer_status_lbl.pack(anchor="e", padx=8, pady=(0, 4))
        transfer_wrap = tk.Frame(mining_transfer_tab, bg="white")
        transfer_wrap.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 6))
        tcols = (
            "parent", "name", "part_count", "sets_carbine", "sets_walls", "sets_chch",
            "sets_current", "sets_after_north", "sets_after", "gain", "transfer_plan", "distribution",
        )
        self._mining_transfer_tree = ttk.Treeview(
            transfer_wrap, columns=tcols, show="headings",
            selectmode="browse", style="Prefix.Treeview",
        )
        transfer_headings = {
            "parent": ("母件 SKU", 88), "name": ("名称", 160), "part_count": ("部件数", 52),
            "sets_carbine": ("Carbine", 56), "sets_walls": ("Walls", 52), "sets_chch": ("CHCH", 52),
            "sets_current": ("现有合计", 64), "sets_after_north": ("北岛调后", 64),
            "sets_after": ("调货后", 56), "gain": ("借调收益", 64),
            "transfer_plan": ("借调方案", 240), "distribution": ("Parts 分布", 200),
        }
        for col, (text, width) in transfer_headings.items():
            self._mining_transfer_tree.heading(col, text=text)
            self._mining_transfer_tree.column(
                col, width=width, anchor="center" if col not in ("name", "distribution") else "w",
            )
        self._mining_transfer_tree.tag_configure("gain", background="#dcfce7")
        self._mining_transfer_tree.tag_configure("alt", background=C_ROW_ALT)
        self._mining_transfer_vscroll = ttk.Scrollbar(
            transfer_wrap, orient="vertical", command=self._mining_transfer_tree.yview,
        )
        self._mining_transfer_tree.configure(yscrollcommand=self._mining_transfer_vscroll.set)
        self._mining_transfer_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._mining_transfer_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._mining_transfer_tree.bind("<Double-1>", self._on_mining_transfer_double_click)

        for widget in (
            mining_inner, self._tab_mining, self._tab_onhold, self._tab_transfer,
            self._mining_tree, self._mining_onhold_tree, self._mining_transfer_tree,
        ):
            widget.bind("<MouseWheel>", self._on_mining_wheel)
            widget.bind("<Button-4>", lambda _e: self._scroll_mining(-1))
            widget.bind("<Button-5>", lambda _e: self._scroll_mining(1))

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
        seen = []
        height = max(self._tree.winfo_height(), ROW_HEIGHT)
        y, step = 0, max(ROW_HEIGHT // 2, 18)
        while y < height + ROW_HEIGHT * 2:
            iid = self._tree.identify_row(y)
            if iid and iid not in seen and iid in self._iid_to_url:
                seen.append(iid)
            y += step
        if seen:
            return seen[: IMAGE_BATCH * 2]
        out = []

        def walk(parent=""):
            for iid in self._tree.get_children(parent):
                if iid in self._iid_to_url:
                    out.append(iid)
                if self._tree.get_children(iid):
                    walk(iid)

        for iid in self._tree.get_children():
            if self._tree.item(iid, "open"):
                walk(iid)
        return out[: IMAGE_BATCH * 2]

    def _is_store_selected(self):
        store = self.store_combo.get() if self.store_combo else self.store_var.get()
        return store != panel_data.ALL_STORES

    def _sync_filter_combos(self):
        for combo, var in getattr(self, "_filter_combos", ()):
            self._sync_island_combo_to_var(combo, var)

    def _discontinue_filter_value(self):
        self._sync_filter_combos()
        return str(self.discontinue_filter_var.get()).strip()

    def _on_filter_combo_change(self):
        self._sync_filter_combos()
        disc_f = self._discontinue_filter_value()
        if not panel_data.EAGER_DISCONTINUED_STOCK:
            need_full = disc_f in ("全部", "已停产")
            if need_full != self._loaded_full_stock:
                if need_full:
                    if self.only_gap_var.get():
                        self.only_gap_var.set(False)
                    if self.only_warehouse_only_var.get():
                        self.only_warehouse_only_var.set(False)
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
            if self.only_warehouse_only_var.get():
                self.only_warehouse_only_var.set(False)
            if self.only_exempted_var.get():
                self.only_exempted_var.set(False)
            self._quick_filter = None
        island_f = self.island_filter_var.get()
        if island_f == "全部":
            self._island_selected_class = None
        else:
            for class_key, label in panel_data.ISLAND_STOCK_CLASSES.items():
                if label == island_f:
                    self._island_selected_class = class_key
                    break
        self._apply_island_quadrant_styles()
        if self._cached_products and self._cached_summary.get("island_stock_supported"):
            self._render_island_table(self._cached_summary.get("store_specific", self._is_store_selected()))
        self._refresh_view()

    def _clear_card_filters(self):
        self.only_gap_var.set(False)
        self.only_warehouse_only_var.set(False)
        self.only_exempted_var.set(False)
        self._quick_filter = None

    def _on_stat_card_click(self, key):
        self._sync_filter_combos()
        disc_f = self._discontinue_filter_value()
        if key in ("gap", "exempted", "warehouse_only") and not self._is_store_selected():
            return
        if key == "warehouse_only" and not self._cached_summary.get("has_storage_data"):
            return

        if key == "gap":
            turning_on = not self.only_gap_var.get()
            self.only_warehouse_only_var.set(False)
            self.only_exempted_var.set(False)
            self.only_gap_var.set(turning_on)
            self._quick_filter = "gap" if turning_on else None
            if turning_on and disc_f == "已停产":
                self.stock_filter_var.set("有货")
                self.display_filter_var.set("未展示")
        elif key == "warehouse_only":
            turning_on = not self.only_warehouse_only_var.get()
            self.only_gap_var.set(False)
            self.only_exempted_var.set(False)
            self.only_warehouse_only_var.set(turning_on)
            if turning_on:
                if disc_f == "已停产":
                    self._status_var.set("「仓有店仓无」仅统计在产 SKU，请先将「停产」改为「在产」")
                    self.only_warehouse_only_var.set(False)
                    return
                self.stock_filter_var.set("有货")
                self.display_filter_var.set("未展示")
                self._quick_filter = "warehouse_only"
            else:
                self._quick_filter = None
        elif key == "exempted":
            turning_on = not self.only_exempted_var.get()
            self.only_gap_var.set(False)
            self.only_warehouse_only_var.set(False)
            self.only_exempted_var.set(turning_on)
            self._quick_filter = "exempted" if turning_on else None
        elif key == "in_stock":
            self._clear_card_filters()
            self.stock_filter_var.set("无货" if self.stock_filter_var.get() == "有货" else "有货")
        if (
            not panel_data.EAGER_DISCONTINUED_STOCK
            and (self._discontinue_filter_value() in ("全部", "已停产"))
            != self._loaded_full_stock
        ):
            self.reload()
            return
        self._refresh_view()

    def _apply_stat_card_styles(self, hint_overrides=None):
        s = self._cached_summary or {}
        store_specific = s.get("store_specific", self._is_store_selected())
        has_storage = bool(s.get("has_storage_data"))
        hint_overrides = hint_overrides or {}

        selected = {
            "gap": self.only_gap_var.get(),
            "warehouse_only": self.only_warehouse_only_var.get(),
            "exempted": self.only_exempted_var.get(),
            "in_stock": (
                self.stock_filter_var.get() == "有货"
                and not self.only_gap_var.get()
                and not self.only_warehouse_only_var.get()
                and not self.only_exempted_var.get()
            ),
        }
        disabled = {
            "gap": not store_specific,
            "warehouse_only": not (store_specific and has_storage),
            "exempted": not store_specific,
        }

        for key, meta in self._stat_card_meta.items():
            if not meta.get("filterable"):
                card = meta["card"]
                card.configure(
                    bg=meta["bg"], highlightbackground=C_CARD_BORDER_IDLE, highlightthickness=1,
                )
                for w in meta["widgets"]:
                    w.configure(bg=meta["bg"], fg=meta["fg"])
                continue

            is_selected = selected.get(key, False)
            is_disabled = disabled.get(key, False)
            if is_disabled:
                bg, fg = C_CARD_DISABLED_BG, C_CARD_DISABLED_FG
                border, thickness, cursor = C_CARD_BORDER_IDLE, 1, "arrow"
                if not store_specific:
                    hint = "请先选择店面"
                elif key == "warehouse_only":
                    hint = "需加载 storage.csv"
                else:
                    hint = "请先选择店面"
            elif is_selected:
                bg, fg = meta["bg_active"], meta["fg"]
                border, thickness, cursor = meta["fg"], 4, "hand2"
                hint = "✓ 筛选中 · 再次点击取消"
            else:
                bg, fg = meta["bg"], meta["fg"]
                border, thickness, cursor = C_CARD_BORDER_IDLE, 2, "hand2"
                hint = hint_overrides.get(key, meta.get("hint_idle", "点击筛选"))

            card = meta["card"]
            card.configure(bg=bg, highlightbackground=border, highlightthickness=thickness, cursor=cursor)
            for w in meta["widgets"]:
                w.configure(bg=bg, fg=fg, cursor=cursor)
            if meta.get("hint"):
                meta["hint"].configure(text=hint)

    def _update_stat_card_highlight(self):
        self._apply_stat_card_styles()

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

    def _schedule_store_prewarm(self, region, current_store, include_discontinued=False):
        """后台预热其余店面（仅预热在产数据，避免与停产加载抢 CPU）。"""
        stores = list(self.store_combo.cget("values")) if self.store_combo else []
        others = [
            s for s in stores
            if s not in (panel_data.ALL_STORES, current_store)
            and self._cache_key(region, s, include_discontinued) not in self._products_cache
        ][:2]
        if not others:
            return
        self._prewarm_token += 1
        token = self._prewarm_token

        def worker():
            warmed = []
            for s in others:
                if token != self._prewarm_token:
                    return
                key = self._cache_key(region, s, include_discontinued)
                if key in self._products_cache:
                    continue
                try:
                    data = panel_data.build_products(
                        store=s, include_discontinued=include_discontinued, region=region,
                    )
                    warmed.append((key, data))
                except Exception:
                    pass

            def apply():
                if token != self._prewarm_token:
                    return
                for key, data in warmed:
                    self._products_cache[key] = data

            if self.root.winfo_exists():
                self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

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
        headers = {
            "User-Agent": "Mozilla/5.0 WarehousePanel/1.4",
            "Accept": "image/*,*/*;q=0.8",
        }
        ctx = ssl.create_default_context()
        unverified = ssl._create_unverified_context()
        for candidate in panel_data.image_url_candidates(url):
            req = urllib.request.Request(panel_data.normalize_url(candidate), headers=headers)
            for context in (ctx, unverified):
                try:
                    with urllib.request.urlopen(req, timeout=20, context=context) as resp:
                        data = resp.read()
                        if data:
                            return data
                except Exception:
                    continue
        raise RuntimeError(f"image fetch failed: {url}")

    def _apply_row_image(self, iid, photo, cache_key):
        if photo:
            self._img_cache[cache_key] = photo
        if self._tree.exists(iid):
            self._tree.item(iid, image=photo or self._placeholder_photo)

    def _schedule_row_image(self, iid, raw, render_token):
        if not raw:
            return
        cache_key = f"{raw}@{THUMB[0]}x{THUMB[1]}"
        if cache_key in self._img_cache:
            self._apply_row_image(iid, self._img_cache[cache_key], cache_key)
            return

        waiters = self._pending_urls.setdefault(raw, set())
        waiters.add((iid, render_token))
        if raw in self._loading_urls:
            return
        self._loading_urls.add(raw)

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
                photo = None

            def apply():
                self._loading_urls.discard(raw)
                targets = list(self._pending_urls.pop(raw, set()))
                if photo:
                    self._img_cache[cache_key] = photo
                for target_iid, token in targets:
                    if token != self._render_token:
                        continue
                    self._apply_row_image(target_iid, photo, cache_key)

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
            region = (self._cached_summary or {}).get("region")
            return panel_data.resolve_product_image(item, self._cached_data_dir, region=region)
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
            xlsx_path = expected.with_suffix(".xlsx")
            if xlsx_path.is_file():
                text = (
                    f"黑名单：当前 0 个  |  已发现 {xlsx_path.name} 但未加载"
                    f"（请点「刷新数据」，或另存为 blacklist.csv：{expected}）"
                )
            else:
                text = (
                    f"黑名单：当前 0 个  |  未找到文件。"
                    f"请将 Excel 另存为 CSV 到：{expected}"
                )
            fg = C_MUTED
        self._blacklist_lbl.configure(text=text, fg=fg)

    def _safe_apply_loaded_data(self, data, region):
        try:
            self._apply_loaded_data(data, region)
        except Exception as exc:
            self._status_var.set(f"界面更新失败：{exc}")
            if "gap" in self._stat_labels:
                self._stat_labels["gap"].configure(text="!")

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
        stock_files = (
            data.get("summary", {}).get("data_files")
            or data.get("summary", {}).get("stock_files")
            or Path(data["stock_path"]).name
        )
        src_line = f"数据源：{src}  |  {stock_files}"
        if fmt:
            src_line += f"（{fmt}）"
        img_n = data.get("summary", {}).get("image_url_count", 0)
        if img_n:
            src_line += f"  |  图片 {img_n}"
        else:
            src_line += "  |  ⚠ 无 ImageUrl"
        self.source_var.set(src_line)
        self._update_blacklist_label()
        self._reload_owner_config(region, data.get("data_dir") or self._cached_data_dir)
        self._prefix_rendered_for = None
        self._owner_rendered_for = None
        self._loaded_full_stock = bool(data.get("summary", {}).get("includes_discontinued"))
        self._refresh_view()
        self.root.after_idle(self._refresh_mining_tabs_if_visible)

    def _refresh_mining_tabs_if_visible(self):
        if not self._notebook:
            return
        try:
            selected = str(self._notebook.select())
        except tk.TclError:
            return
        if self._tab_onhold and selected == str(self._tab_onhold):
            self._render_on_hold_analysis()
        elif self._tab_transfer and selected == str(self._tab_transfer):
            self._render_mining_transfer_table()
        elif self._tab_mining and selected == str(self._tab_mining):
            self._render_mining_table()

    def _cache_key(self, region, store, include_discontinued):
        if panel_data.EAGER_DISCONTINUED_STOCK:
            return (region, store)
        return (region, store, bool(include_discontinued))

    def _needs_discontinued_stock(self):
        if panel_data.EAGER_DISCONTINUED_STOCK:
            return True
        return self._discontinue_filter_value() in ("全部", "已停产")

    def reload(self, force=False):
        self._sync_filter_combos()
        self._reload_token += 1
        token = self._reload_token
        region = self._current_region()
        store = self.store_combo.get() if self.store_combo else self.store_var.get()
        need_disc = self._needs_discontinued_stock()
        cache_key = self._cache_key(region, store, need_disc)

        if force:
            self._products_cache = {}
            panel_data.clear_region_cache(region)

        if not force and cache_key in self._products_cache:
            self._safe_apply_loaded_data(self._products_cache[cache_key], region)
            return

        loading_msg = "正在计算店面数据…"
        if need_disc:
            loading_msg += "（含停产）"
        self._show_loading_state(loading_msg)
        self._set_controls_state(False)
        self._set_busy(True)

        def worker():
            return panel_data.build_products(
                store=store, only_gap=False, include_discontinued=need_disc,
                region=region, force_refresh=force,
            )

        def done(err, data):
            try:
                if token != self._reload_token:
                    return
                if err:
                    self._status_var.set(f"加载失败：{err}")
                    if "gap" in self._stat_labels:
                        self._stat_labels["gap"].configure(text="!")
                    return
                self._products_cache[cache_key] = data
                self._safe_apply_loaded_data(data, region)
                if store != panel_data.ALL_STORES:
                    self._schedule_store_prewarm(region, store, include_discontinued=False)
            finally:
                if token == self._reload_token:
                    self._set_busy(False)
                    self._set_controls_state(True)

        self._run_bg(worker, done)

    def _apply_client_filters(self, products):
        q = self.search_var.get().strip().lower()
        stock_f = self.stock_filter_var.get()
        display_f = self.display_filter_var.get()
        disc_f = self._discontinue_filter_value()
        only_gap = self.only_gap_var.get()
        only_warehouse_only = self.only_warehouse_only_var.get()
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
            if only_gap:
                if p.get("discontinued"):
                    if not (p.get("in_stock") and not p.get("displayed") and not p.get("exempted")):
                        continue
                elif not p.get("gap"):
                    continue
            if only_warehouse_only and not (p.get("warehouse_only") and not p.get("exempted")):
                continue
            if only_exempted and not p.get("exempted"):
                continue
            if (
                stock_f == "有货"
                and display_f == "未展示"
                and not only_exempted
                and not only_gap
                and not only_warehouse_only
                and p.get("exempted")
            ):
                continue
            island_f = self.island_filter_var.get()
            if island_f != "全部":
                if p.get("island_stock_label") != island_f:
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
        self._sync_filter_combos()
        disc_f = self._discontinue_filter_value()
        has_storage = bool(s.get("has_storage_data"))
        if (not store_specific or not has_storage) and self.only_warehouse_only_var.get():
            self.only_warehouse_only_var.set(False)
            if self._quick_filter == "warehouse_only":
                self._quick_filter = None

        def pct(v):
            return "-" if v is None else f"{v:.1f}%"

        hint_overrides = {}
        if store_specific:
            gap_stats = self._compute_gap_stats(self._cached_products, disc_f)
            self._stat_labels["gap"].configure(
                text=str(gap_stats["main_count"]), font=("Segoe UI", 18, "bold"),
            )
            if not self.only_gap_var.get():
                hint_overrides["gap"] = gap_stats["hint"]
            exempted_n = sum(
                1 for p in self._cached_products
                if p.get("exempted") and self._disc_matches(p, disc_f)
            )
            self._stat_labels["exempted"].configure(text=str(exempted_n), font=("Segoe UI", 18, "bold"))
            wh_n = sum(
                1 for p in self._cached_products
                if p.get("warehouse_only") and not p.get("exempted") and self._disc_matches(p, disc_f)
            )
            self._stat_labels["warehouse_only"].configure(text=str(wh_n), font=("Segoe UI", 18, "bold"))
        else:
            self._stat_labels["gap"].configure(text="—", font=("Segoe UI", 14, "bold"))
            self._stat_labels["exempted"].configure(text="—", font=("Segoe UI", 14, "bold"))
            self._stat_labels["warehouse_only"].configure(text="—", font=("Segoe UI", 14, "bold"))
        self._stat_labels["in_stock"].configure(text=str(s.get("in_stock_count", 0)))
        self._stat_labels["rate"].configure(text=pct(s.get("in_stock_rate")))
        self._stat_labels["total"].configure(text=str(s.get("total_non_discontinue", 0)))
        stock_line = (
            f"店面：{s.get('store', '-')}  |  "
            f"库存文件：{s.get('stock_files', 'stock.csv')}  |  "
            f"仓：{s.get('stock_sources', '-')}"
        )
        img_n = s.get("image_url_count", 0)
        if img_n:
            stock_line += f"  |  图片URL：{img_n}"
        else:
            stock_line += "  |  ⚠ stock.csv 无 ImageUrl，请重新导出 SQL"
        if s.get("has_storage_data"):
            if store_specific:
                wh = s.get("storage_warehouse")
                if wh:
                    stock_line += f"  |  店后仓：{wh}"
                stock_line += f"  |  店仓SKU：{s.get('in_storage_count', 0)}"
                if s.get("warehouse_only_count"):
                    stock_line += f"  |  仓有店仓无：{s['warehouse_only_count']}"
                if s.get("ready_not_displayed_count"):
                    stock_line += f"  |  双有未陈列：{s['ready_not_displayed_count']}"
            else:
                mapped = s.get("storage_map") or {}
                stock_line += (
                    f"  |  店后仓已加载：{len(mapped)} 个仓 / "
                    f"全区域店仓SKU {s.get('all_storage_sku_count', 0)}（请选店面看明细）"
                )
            unmapped = s.get("storage_unmapped") or []
            if unmapped:
                stock_line += f"  |  ⚠ 未映射店后仓：{len(unmapped)} 个"
        else:
            stock_line += "  |  ⚠ 无 storage.csv（请执行 storage.sql）"
        self._stock_source_lbl.configure(text=stock_line)
        self._update_blacklist_label()
        self._apply_stat_card_styles(hint_overrides)

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
        if disc_f == "已停产" and len(filtered) == 0:
            if disc_total > 0 and self.stock_filter_var.get() == "有货":
                self.result_count_var.set(
                    f"显示 0 / 停产 {disc_total} 条（可尝试将「库存」改为「全部」）"
                )
            elif disc_total == 0 and not self._loaded_full_stock:
                self.result_count_var.set("停产数据未加载，请再次选择「停产 → 已停产」或点「刷新数据」")
        self._status_var.set(f"正在渲染 {len(filtered)} 条…")
        self.root.update_idletasks()
        self._render_tree(filtered)
        prefix_key = (s.get("region"), s.get("store"))
        if (
            prefix_key != self._prefix_rendered_for
            or prefix_key != self._owner_rendered_for
            or prefix_key != self._island_rendered_for
            or prefix_key != self._mining_rendered_for
        ):
            self.root.after_idle(
                lambda: self._render_summary_tables_deferred(prefix_key, store_specific)
            )
        else:
            hint = ""
            if len(filtered) > AUTO_EXPAND_ALL_GROUPS:
                hint = " · 系列已折叠，可点「展开全部系列」"
            self._status_var.set(
                f"就绪 · {len(filtered)} 条{hint}"
                + ("" if self._images_enabled() else " · 双击行或点「查看图片」")
            )

    def _render_summary_tables_deferred(self, prefix_key, store_specific):
        if prefix_key != (self._cached_summary.get("region"), self._cached_summary.get("store")):
            return
        self._render_prefix_table(store_specific)
        self._render_owner_table(store_specific)
        self._render_island_quadrants(store_specific)
        self._render_mining_panels()
        self._prefix_rendered_for = prefix_key
        self._owner_rendered_for = prefix_key
        self._island_rendered_for = prefix_key
        self._mining_rendered_for = prefix_key
        n = len(self._apply_client_filters(self._cached_products))
        self._status_var.set(
            f"就绪 · {n} 条"
            + ("" if self._images_enabled() else " · 可点「查看图片」")
        )

    def _prefix_row_tag(self, row, index):
        rate = row.get("in_stock_rate")
        if rate is None:
            return ("alt",) if index % 2 == 1 else ()
        if rate >= 50:
            return ("ok",)
        if rate >= 20:
            return ("warn",)
        if row.get("in_stock_count", 0) > 0 or rate > 0:
            return ("low",)
        return ("alt",) if index % 2 == 1 else ()

    def _render_prefix_table(self, store_specific=True):
        if not self._prefix_tree:
            return
        rows = panel_data.aggregate_by_sku_prefix(
            self._cached_products, store_specific=store_specific,
            owner_config=self._cached_owner_config,
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
                    row.get("owner") or "-",
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

    def _owner_rate_tag(self, row, index):
        rate = row.get("in_stock_rate")
        if rate is None:
            return ("alt",) if index % 2 == 1 else ()
        if rate >= 50:
            return ("ok",)
        if rate >= 20:
            return ("warn",)
        return ("low",)

    def _on_notebook_tab_change(self, _event=None):
        if not self._notebook:
            return
        try:
            selected = self._notebook.select()
        except Exception:
            return
        mining_tabs = tuple(
            str(t) for t in (
                self._tab_mining, self._tab_onhold, self._tab_transfer,
            ) if t
        )
        if not self._cached_products and selected not in mining_tabs:
            return
        store_specific = self._cached_summary.get("store_specific", self._is_store_selected())
        if selected == str(self._tab_owner):
            self._render_owner_table(store_specific)
        elif selected == str(self._tab_island):
            self._render_island_quadrants(store_specific)
        elif selected == str(self._tab_mining):
            self._render_mining_table()
        elif self._tab_onhold and selected == str(self._tab_onhold):
            self._render_on_hold_analysis()
        elif self._tab_transfer and selected == str(self._tab_transfer):
            self._render_mining_transfer_table()

    def _mining_kind_key(self):
        kind = self._mining_kind_var.get()
        if kind == "On Hold":
            return "on_hold"
        if kind == "配件":
            return "parts"
        return "all"

    def _render_mining_table(self):
        if not self._mining_tree:
            return
        region = self._cached_summary.get("region") or self._current_region()
        try:
            bundle = panel_data.get_region_bundle(region)
        except Exception:
            bundle = {}
        kind = self._mining_kind_key()
        rows = panel_data.list_mining_inventory(bundle, kind)
        if self._mining_tree.get_children():
            self._mining_tree.delete(*self._mining_tree.get_children())
        for idx, row in enumerate(rows):
            wh_text = "、".join(
                f"{w.get('warehouse', '')} {int(w.get('qty', 0))}"
                for w in (row.get("warehouses") or [])[:4]
            )
            if len(row.get("warehouses") or []) > 4:
                wh_text += "…"
            tag = "hold" if row.get("kind") == "On Hold" else "part"
            if idx % 2 == 1:
                tag = (tag, "alt")
            self._mining_tree.insert(
                "", tk.END,
                values=(
                    row.get("kind") or "",
                    row.get("code") or "",
                    row.get("name") or "",
                    row.get("family") or "",
                    int(row.get("qty") or 0),
                    row.get("detail") or "-",
                    wh_text or "-",
                ),
                tags=tag,
            )
        oh_n = len(bundle.get("on_hold_by_code") or {})
        pt_n = len(bundle.get("parts_by_code") or {})
        oh_rows = int(bundle.get("on_hold_row_count") or 0)
        pt_rows = int(bundle.get("parts_row_count") or 0)
        if self._mining_status_lbl:
            oh_diag = panel_data.diagnose_on_hold_bundle(bundle)
            if oh_n or pt_n:
                self._mining_status_lbl.configure(
                    text=f"On Hold {oh_n} SKU · 配件 {pt_n} SKU · 当前显示 {len(rows)} 条",
                )
            elif oh_rows or pt_rows:
                parts_hint = (
                    f"parts {pt_rows} 行未识别 SKU"
                    if pt_rows and not pt_n else f"parts {pt_rows} 行"
                )
                oh_hint = oh_diag or f"on_hold {oh_rows} 行未识别 SKU"
                self._mining_status_lbl.configure(text=f"{oh_hint} · {parts_hint} · 请点「刷新数据」")
            else:
                self._mining_status_lbl.configure(
                    text=oh_diag or "请执行 on_hold.txt / parts.txt 导出到 Output-NZ 后点「刷新数据」",
                )

    def _render_mining_panels(self):
        self._render_mining_table()
        try:
            self._render_on_hold_analysis()
        except Exception as exc:
            if self._onhold_status_lbl:
                self._onhold_status_lbl.configure(
                    text=f"On Hold 渲染失败（v{APP_VERSION}）：{exc}",
                )
        try:
            self._render_mining_transfer_table()
        except Exception as exc:
            if self._transfer_status_lbl:
                self._transfer_status_lbl.configure(text=f"借调渲染失败：{exc}")

    def _catalog_by_norm(self):
        out = {}
        for product in self._cached_products or []:
            norm = product.get("norm_code") or panel_data._norm_code(product.get("code"))
            if norm:
                out[norm] = product
        return out

    def _clear_onhold_summary_cards(self):
        if not self._onhold_summary_frame:
            return
        for child in self._onhold_summary_frame.winfo_children():
            child.destroy()

    def _render_on_hold_summary(self, status_rows):
        self._clear_onhold_summary_cards()
        if not self._onhold_summary_frame:
            return
        if not status_rows:
            try:
                bundle = panel_data.get_region_bundle(
                    self._cached_summary.get("region") or self._current_region(),
                )
            except Exception:
                bundle = {}
            diag = panel_data.diagnose_on_hold_bundle(bundle) or "暂无 On Hold 数据"
            tk.Label(
                self._onhold_summary_frame, text=diag, bg="white", fg="#b45309",
                font=("Segoe UI", 9), wraplength=920, justify="left",
            ).pack(anchor="w")
            return
        for row in status_rows[:8]:
            text = (
                f"{row.get('status')}：{row.get('sku_count', 0)} SKU · "
                f"{row.get('total_qty', 0)} 件 · {row.get('row_count', 0)} 行"
            )
            tk.Label(
                self._onhold_summary_frame, text=text, bg="#fef3c7", fg="#92400e",
                font=("Segoe UI", 9), padx=8, pady=4,
            ).pack(side=tk.LEFT, padx=(0, 8), pady=2)

    def _apply_onhold_row_image(self, iid, photo, cache_key):
        if photo:
            self._img_cache[cache_key] = photo
        if self._mining_onhold_tree and self._mining_onhold_tree.exists(iid):
            self._mining_onhold_tree.item(iid, image=photo or self._placeholder_photo)

    def _schedule_onhold_row_image(self, iid, raw, render_token):
        if not raw or not self._images_enabled():
            return
        cache_key = f"{raw}@{THUMB[0]}x{THUMB[1]}"
        if cache_key in self._img_cache:
            self._apply_onhold_row_image(iid, self._img_cache[cache_key], cache_key)
            return
        waiters = self._pending_urls.setdefault(raw, set())
        waiters.add((f"onhold:{iid}", render_token))
        if raw in self._loading_urls:
            return
        self._loading_urls.add(raw)

        def worker():
            photo = None
            try:
                with self._image_semaphore:
                    if str(raw).lower().startswith(("http://", "https://")):
                        data = self._fetch_image_bytes(raw)
                        if Image is not None:
                            photo = self._pil_to_photo(Image.open(io.BytesIO(data)))
                    else:
                        path = Path(raw)
                        if not path.is_absolute():
                            path = Path(panel_data.ROOT_DIR) / path
                        if path.is_file() and Image is not None:
                            photo = self._pil_to_photo(Image.open(path))
            except Exception:
                photo = None

            def apply():
                self._loading_urls.discard(raw)
                targets = list(self._pending_urls.pop(raw, set()))
                if photo:
                    self._img_cache[cache_key] = photo
                for target, token in targets:
                    if not str(target).startswith("onhold:") or token != self._mining_onhold_render_token:
                        continue
                    oid = str(target).split(":", 1)[1]
                    self._apply_onhold_row_image(oid, photo, cache_key)

            if self.root.winfo_exists():
                self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _on_onhold_status_change(self, _event=None):
        self.root.after_idle(self._apply_on_hold_status_filter)

    def _apply_on_hold_status_filter(self):
        self._sync_island_combo_to_var(self._onhold_status_combo, self._onhold_status_filter_var)
        self._sync_island_combo_to_var(self._onhold_days_combo, self._onhold_days_filter_var)
        self._render_on_hold_analysis()

    def _onhold_status_filter_value(self):
        self._sync_island_combo_to_var(self._onhold_status_combo, self._onhold_status_filter_var)
        return str(self._onhold_status_filter_var.get()).strip()

    def _onhold_days_filter_value(self):
        self._sync_island_combo_to_var(self._onhold_days_combo, self._onhold_days_filter_var)
        return str(self._onhold_days_filter_var.get()).strip()

    def _sort_onhold_rows(self, rows):
        col = getattr(self, "_onhold_sort_col", None)
        if col and col in ONHOLD_SORTABLE_COLS:
            key_fn = ONHOLD_SORTABLE_COLS[col]
            reverse = bool(getattr(self, "_onhold_sort_reverse", False))
            return sorted(rows, key=key_fn, reverse=reverse)
        return rows

    def _on_onhold_sort_column(self, col):
        if col not in ONHOLD_SORTABLE_COLS:
            return
        sort_col = getattr(self, "_onhold_sort_col", None)
        if sort_col == col:
            self._onhold_sort_reverse = not bool(getattr(self, "_onhold_sort_reverse", False))
        else:
            self._onhold_sort_col = col
            self._onhold_sort_reverse = col in ONHOLD_NUMERIC_SORT_COLS
        self._render_on_hold_analysis()

    def _render_on_hold_analysis(self):
        if not self._mining_onhold_tree:
            return
        region = self._cached_summary.get("region") or self._current_region()
        try:
            bundle = panel_data.get_region_bundle(region)
        except Exception:
            bundle = {}
        bl = bundle.get("blacklist") or set()
        status_rows = panel_data.aggregate_on_hold_by_status(
            on_hold_rows=bundle.get("on_hold_rows"),
            on_hold_by_code=bundle.get("on_hold_by_code"),
            blacklist=bl,
        )
        self._render_on_hold_summary(status_rows)
        options = ["全部状态"] + [r["status"] for r in status_rows]
        if self._onhold_status_combo:
            self._onhold_status_combo.configure(values=options)
            self._sync_island_combo_to_var(self._onhold_status_combo, self._onhold_status_filter_var)
            shown = str(self._onhold_status_combo.get()).strip()
            if shown and shown in options:
                self._onhold_status_filter_var.set(shown)
            elif self._onhold_status_filter_var.get() not in options:
                self._onhold_status_filter_var.set("全部状态")
        status_f = self._onhold_status_filter_value()
        days_f = self._onhold_days_filter_value()
        min_days = panel_data.parse_on_hold_min_days(days_f)
        rows, total_matched = panel_data.list_on_hold_analysis(
            bundle,
            status_filter=status_f,
            catalog_by_norm=self._catalog_by_norm(),
            min_hold_days=min_days,
            blacklist=bl,
        )
        try:
            rows = self._sort_onhold_rows(rows)
        except Exception:
            pass
        ui_cap = panel_data.ON_HOLD_UI_MAX_ROWS
        total_sorted = len(rows)
        if ui_cap and total_sorted > ui_cap:
            rows = rows[:ui_cap]
        self._mining_onhold_render_token += 1
        token = self._mining_onhold_render_token
        self._mining_onhold_row_data = {}
        if self._mining_onhold_tree.get_children():
            self._mining_onhold_tree.delete(*self._mining_onhold_tree.get_children())

        def _onhold_status_hint(inserted):
            if not rows and not total_matched:
                diag = panel_data.diagnose_on_hold_bundle(bundle)
                return diag or "On Hold 0 条"
            if not rows and total_matched:
                return (
                    f"筛选后 0 条（原始 {total_matched} 条）· 请试「全部状态」或调整冻结天数"
                    + (f" · 当前状态：{status_f}" if status_f else "")
                )
            no_date = sum(1 for r in rows if r.get("hold_days") is None)
            hint = f"显示 {inserted} / 共 {total_matched} 条"
            if total_sorted > inserted:
                hint += f"（界面最多 {ui_cap} 条，已按当前排序截取）"
            if status_f and status_f != "全部状态":
                hint += f" · {status_f}"
            if min_days:
                hint += f" · 冻结≥{min_days}天"
            sort_col = getattr(self, "_onhold_sort_col", None)
            if sort_col:
                order = "降序" if getattr(self, "_onhold_sort_reverse", False) else "升序"
                hint += f" · 按{sort_col} {order}"
            if total_matched > total_sorted:
                hint += f" · 解析上限 {panel_data.ON_HOLD_ANALYSIS_MAX_ROWS} 条"
            if bl:
                hint += f" · 已排除黑名单 SKU"
            if no_date and rows and min_days:
                hint += " · 无日期行已排除"
            elif no_date and rows:
                hint += f" · {no_date} 条无冻结日期"
            if total_sorted > ON_HOLD_IMAGE_MAX_ROWS:
                hint += f" · 仅前 {ON_HOLD_IMAGE_MAX_ROWS} 行加载缩略图"
            return hint

        if self._onhold_status_lbl and not rows:
            self._onhold_status_lbl.configure(text=_onhold_status_hint(0))

        def fill_onhold_batch(start=0):
            if token != self._mining_onhold_render_token:
                return
            end = min(start + ON_HOLD_TREE_BATCH, len(rows))
            for idx in range(start, end):
                row = rows[idx]
                try:
                    hold_days = row.get("hold_days")
                    hold_days_text = str(hold_days) if hold_days is not None else "-"
                    qty_raw = row.get("qty")
                    try:
                        qty_disp = int(float(qty_raw or 0))
                    except (TypeError, ValueError):
                        qty_disp = 0
                    iid = self._mining_onhold_tree.insert(
                        "", tk.END,
                        image=self._placeholder_photo,
                        values=(
                            row.get("code") or "",
                            row.get("name") or "",
                            row.get("status") or "-",
                            row.get("order_no") or "-",
                            row.get("ticket_no") or "-",
                            hold_days_text,
                            row.get("hold_since") or "-",
                            qty_disp,
                            row.get("warehouse") or "-",
                        ),
                        tags=("hold", "alt") if idx % 2 else ("hold",),
                    )
                    self._mining_onhold_row_data[iid] = row
                    if idx < ON_HOLD_IMAGE_MAX_ROWS:
                        raw = self._image_url_for_item(row)
                        if raw:
                            self._schedule_onhold_row_image(iid, raw, token)
                except Exception:
                    continue
            if end < len(rows):
                if self._onhold_status_lbl:
                    self._onhold_status_lbl.configure(
                        text=f"加载中 {end} / {len(rows)} 行…",
                    )
                self.root.after(1, lambda s=end: fill_onhold_batch(s))
            elif self._onhold_status_lbl:
                self._onhold_status_lbl.configure(text=_onhold_status_hint(len(rows)))

        if rows:
            self.root.after_idle(lambda: fill_onhold_batch(0))

    def _open_onhold_selected_image(self):
        sel = self._mining_onhold_tree.selection() if self._mining_onhold_tree else ()
        if not sel:
            if messagebox:
                messagebox.showinfo("查看图片", "请先选中一行 On Hold 产品。")
            return
        item = (self._mining_onhold_row_data or {}).get(sel[0])
        if not item:
            return
        self._open_image_for_item(item)

    def _on_mining_onhold_double_click(self, _event=None):
        sel = self._mining_onhold_tree.selection() if self._mining_onhold_tree else ()
        if not sel:
            return
        item = (self._mining_onhold_row_data or {}).get(sel[0])
        if not item:
            return
        if self._images_enabled():
            self._open_image_for_item(item)
            return
        self.search_var.set(str(item.get("code") or ""))
        if self._notebook:
            self._notebook.select(self._tab_products)
        self._refresh_view()

    def _render_mining_transfer_table(self):
        if not self._mining_transfer_tree:
            return
        region = self._cached_summary.get("region") or self._current_region()
        try:
            bundle = panel_data.get_region_bundle(region)
        except Exception:
            bundle = {}
        raw_parts = bundle.get("parts_rows") or []
        detail = bundle.get("parts_detail_rows") or []
        bom = bundle.get("parts_kit_bom") or {}
        rows = panel_data.analyze_parts_transfer(
            raw_parts or detail,
            parts_kit_bom=bom,
            warehouse_hints=bundle.get("warehouse_transfer_hints"),
            warehouse_overrides=bundle.get("warehouse_bucket_overrides"),
        )
        if self._mining_transfer_tree.get_children():
            self._mining_transfer_tree.delete(*self._mining_transfer_tree.get_children())
        for idx, row in enumerate(rows):
            gain = int(row.get("transfer_gain") or 0)
            tags = ("gain",) if gain > 0 else ()
            if idx % 2 == 1 and not tags:
                tags = ("alt",)
            elif idx % 2 == 1 and tags:
                tags = ("gain",)
            self._mining_transfer_tree.insert(
                "", tk.END,
                values=(
                    row.get("parent") or "",
                    row.get("name") or "",
                    row.get("part_count") or 0,
                    row.get("sets_carbine") or 0,
                    row.get("sets_walls") or 0,
                    row.get("sets_chch") or 0,
                    row.get("sets_current_total") or 0,
                    row.get("sets_after_north") or 0,
                    row.get("sets_after_transfer") or 0,
                    gain,
                    row.get("transfer_plan") or "-",
                    row.get("parts_distribution") or "-",
                ),
                tags=tags,
            )
        gain_n = sum(1 for r in rows if (r.get("transfer_gain") or 0) > 0)
        if self._transfer_status_lbl:
            if rows:
                self._transfer_status_lbl.configure(
                    text=f"借调可增收 {gain_n} 个母件 · 共 {len(rows)} 条（按借调收益排序）",
                )
            elif int(bundle.get("parts_row_count") or 0):
                hint = panel_data.describe_parts_transfer_gap(detail or [])
                self._transfer_status_lbl.configure(
                    text=(
                        f"{hint}。"
                        "宽表需 ProductSku + PartName + 三仓库存列；或配置 Data-NZ/parts_kits.csv。"
                    ),
                )
            else:
                self._transfer_status_lbl.configure(
                    text="请导出 parts.csv 后点「刷新数据」",
                )

    def _on_mining_transfer_double_click(self, _event=None):
        sel = self._mining_transfer_tree.selection() if self._mining_transfer_tree else ()
        if not sel:
            return
        values = self._mining_transfer_tree.item(sel[0], "values")
        if not values:
            return
        self.search_var.set(str(values[0]))
        if self._notebook:
            self._notebook.select(self._tab_products)
        self._refresh_view()

    def _scroll_mining(self, direction):
        tree = self._mining_tree
        if self._notebook:
            try:
                selected = str(self._notebook.select())
                if self._tab_onhold and selected == str(self._tab_onhold):
                    tree = self._mining_onhold_tree
                elif self._tab_transfer and selected == str(self._tab_transfer):
                    tree = self._mining_transfer_tree
            except tk.TclError:
                pass
        if tree:
            tree.yview_scroll(direction * SCROLL_UNITS, "units")

    def _on_mining_wheel(self, event):
        step = -1 if event.delta > 0 else 1
        self._scroll_mining(step)

    def _on_mining_double_click(self, _event=None):
        sel = self._mining_tree.selection() if self._mining_tree else ()
        if not sel:
            return
        values = self._mining_tree.item(sel[0], "values")
        if not values:
            return
        self.search_var.set(str(values[1]))
        if self._notebook:
            self._notebook.select(self._tab_products)
        self._refresh_view()

    def _sync_island_filter_var(self, class_key=None):
        if class_key:
            self.island_filter_var.set(panel_data.ISLAND_STOCK_CLASSES[class_key])
        else:
            self.island_filter_var.set("全部")

    def _apply_island_quadrant_styles(self):
        for class_key, meta in self._island_quadrant_meta.items():
            selected = self._island_selected_class == class_key
            bg = meta["bg_active"] if selected else meta["bg"]
            border = meta["fg"] if selected else C_CARD_BORDER_IDLE
            thickness = 4 if selected else 2
            meta["card"].configure(bg=bg, highlightbackground=border, highlightthickness=thickness)
            for widget in meta["widgets"]:
                widget.configure(bg=bg, fg=meta["fg"])
            hint = meta.get("hint")
            if hint:
                hint.configure(text="✓ 筛选中" if selected else "点击筛选")

    def _on_island_scope_change(self):
        view_mode = self._island_view_mode_var.get()
        if view_mode != "总览":
            self._island_owner_filter_var.set("全部负责人")
            self._island_channel_filter_var.set("全部渠道")
        self._island_selected_class = None
        self._sync_island_filter_var(None)
        self._update_island_owner_channel_combos(reset_channel=True)
        self._render_island_quadrants(self._cached_summary.get("store_specific", self._is_store_selected()))

    def _on_island_owner_change(self):
        self._island_selected_class = None
        self._sync_island_filter_var(None)
        self._update_island_channel_combo(reset=True)
        self._render_island_quadrants(self._cached_summary.get("store_specific", self._is_store_selected()))

    def _on_island_channel_change(self, _event=None):
        # Windows 下等下拉框提交选中值后再刷新，避免立刻被重置
        self.root.after_idle(self._apply_island_channel_filter)

    def _apply_island_channel_filter(self):
        self._sync_island_combo_to_var(self._island_channel_combo, self._island_channel_filter_var)
        self._island_selected_class = None
        self._sync_island_filter_var(None)
        self._render_island_quadrants(self._cached_summary.get("store_specific", self._is_store_selected()))

    @staticmethod
    def _sync_island_combo_to_var(combo, var):
        """ttk Combobox 在 Windows 上常只改显示、不写 StringVar，筛选前强制同步。"""
        if not combo or not var:
            return
        try:
            shown = str(combo.get()).strip()
        except tk.TclError:
            return
        if shown:
            var.set(shown)

    def _island_owner_filter_value(self):
        self._sync_island_combo_to_var(self._island_owner_combo, self._island_owner_filter_var)
        owner = str(self._island_owner_filter_var.get()).strip()
        if owner in ("", "全部负责人"):
            return None
        return owner

    def _island_channel_filter_value(self):
        self._sync_island_combo_to_var(self._island_channel_combo, self._island_channel_filter_var)
        channel = str(self._island_channel_filter_var.get()).strip()
        if channel in ("", "全部渠道"):
            return None
        return channel

    def _update_island_channel_combo(self, reset=False):
        owner = self._island_owner_filter_var.get()
        if owner in ("", "全部负责人"):
            channels = ["全部渠道"]
            channel_state = "disabled"
        else:
            opts = panel_data.list_island_channel_options(
                self._cached_products, self._cached_owner_config, owner=owner,
            )
            channels = ["全部渠道"] + [str(c) for c in opts]
            channel_state = "readonly" if opts else "disabled"
        if self._island_channel_combo:
            self._island_channel_combo.configure(values=channels, state=channel_state)
        current = self._island_channel_filter_var.get()
        if reset or current not in channels:
            self._island_channel_filter_var.set("全部渠道")

    def _update_island_owner_channel_combos(self, reset_channel=False):
        owners = ["全部负责人"] + list(panel_data.list_owner_channel_tree(self._cached_owner_config)[0])
        if self._island_owner_combo:
            self._island_owner_combo.configure(values=owners)
            if self._island_owner_filter_var.get() not in owners:
                self._island_owner_filter_var.set("全部负责人")
                reset_channel = True
        self._update_island_channel_combo(reset=reset_channel)

    def _island_scope_filters(self):
        view_mode = self._island_view_mode_var.get()
        if view_mode != "总览":
            return None, None
        return self._island_owner_filter_value(), self._island_channel_filter_value()

    def _update_main_island_quadrant(self, report):
        counts = report.get("counts") or {}
        percents = report.get("percents") or {}
        for class_key, lbl in self._island_quadrant_cards.items():
            lbl.configure(text=str(counts.get(class_key, 0)))
        for class_key, lbl in self._island_quadrant_pcts.items():
            lbl.configure(text=f"{percents.get(class_key, 0.0):.1f}%")

    def _clear_island_group_widgets(self):
        if not self._island_groups_frame:
            return
        for child in self._island_groups_frame.winfo_children():
            child.destroy()

    def _build_mini_island_quadrant(self, parent, title, counts, percents, meta):
        wrap = tk.Frame(parent, bg="white", padx=4, pady=4)
        tk.Label(
            wrap, text=title, bg="white", fg=C_TEXT, font=("Segoe UI", 9, "bold"), anchor="w",
        ).pack(anchor="w")
        tk.Label(
            wrap, text=f"共 {sum(counts.values())} SKU", bg="white", fg=C_MUTED,
            font=("Segoe UI", 8), anchor="w",
        ).pack(anchor="w", pady=(0, 4))
        grid = tk.Frame(wrap, bg="white")
        grid.pack(fill=tk.X)
        for row_idx, row_keys in enumerate(panel_data.ISLAND_STOCK_GRID):
            for col_idx, class_key in enumerate(row_keys):
                bg, _bg_active, fg = ISLAND_QUADRANT_STYLE[class_key]
                cell = tk.Frame(grid, bg=bg, padx=8, pady=6, cursor="hand2")
                cell.grid(row=row_idx, column=col_idx, padx=3, pady=3, sticky="nsew")
                title_lbl = tk.Label(
                    cell, text=panel_data.ISLAND_STOCK_CLASSES[class_key], bg=bg, fg=fg,
                    font=("Segoe UI", 8, "bold"), cursor="hand2",
                )
                title_lbl.pack(anchor="w")
                count_lbl = tk.Label(
                    cell, text=str(counts.get(class_key, 0)), bg=bg, fg=fg,
                    font=("Segoe UI", 14, "bold"), cursor="hand2",
                )
                count_lbl.pack(anchor="w")
                pct_lbl = tk.Label(
                    cell, text=f"{percents.get(class_key, 0.0):.1f}%", bg=bg, fg=fg,
                    font=("Segoe UI", 8), cursor="hand2",
                )
                pct_lbl.pack(anchor="w")
                for widget in (cell, title_lbl, count_lbl, pct_lbl):
                    widget.bind(
                        "<Button-1>",
                        lambda _e, key=class_key, m=meta: self._on_island_group_cell_click(m, key),
                    )
        return wrap

    def _on_island_group_cell_click(self, meta, class_key):
        self._island_view_mode_var.set("总览")
        if meta.get("owner"):
            self._island_owner_filter_var.set(meta["owner"])
            self._update_island_channel_combo(reset=False)
        if meta.get("channel"):
            self._island_channel_filter_var.set(meta["channel"])
        self._island_selected_class = class_key
        self._sync_island_filter_var(class_key)
        self._render_island_quadrants(self._cached_summary.get("store_specific", self._is_store_selected()))
        self._refresh_view()

    def _render_island_group_quadrants(self, store_specific=True):
        view_mode = self._island_view_mode_var.get()
        group_by = "owner" if view_mode == "按负责人" else "channel"
        groups = panel_data.aggregate_island_quadrants_grouped(
            self._cached_products, group_by, self._cached_owner_config,
        )
        self._clear_island_group_widgets()
        if not groups:
            tk.Label(
                self._island_groups_frame,
                text="未找到 channel_owner(s).csv 配置，无法按负责人/渠道分组。",
                bg="white", fg=C_MUTED, font=("Segoe UI", 9),
            ).pack(anchor="w", padx=8, pady=8)
            return
        row_frame = None
        for idx, group in enumerate(groups):
            if idx % 2 == 0:
                row_frame = tk.Frame(self._island_groups_frame, bg="white")
                row_frame.pack(fill=tk.X, pady=(0, 8))
            meta = {"owner": group.get("owner"), "channel": group.get("channel")}
            mini = self._build_mini_island_quadrant(
                row_frame, group["label"], group["counts"], group["percents"], meta,
            )
            mini.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
        if self._island_status_lbl:
            self._island_status_lbl.configure(
                text=f"{view_mode} · {len(groups)} 组 · 点击小象限可筛选下方列表",
            )

    def _on_island_quadrant_click(self, class_key):
        if self._island_selected_class == class_key:
            self._clear_island_quadrant_filter()
            return
        self._island_selected_class = class_key
        self._sync_island_filter_var(class_key)
        self._apply_island_quadrant_styles()
        self._render_island_table()
        self._refresh_view()

    def _clear_island_quadrant_filter(self):
        self._island_selected_class = None
        self._island_owner_filter_var.set("全部负责人")
        self._island_channel_filter_var.set("全部渠道")
        self._sync_island_filter_var(None)
        self._apply_island_quadrant_styles()
        self._render_island_quadrants(self._cached_summary.get("store_specific", self._is_store_selected()))
        self._refresh_view()

    def _open_island_selection_in_products(self):
        if self._notebook:
            self._notebook.select(self._tab_products)
        self._refresh_view()

    def _island_row_tag(self, product, index):
        cls = product.get("island_stock_class")
        if cls == "both":
            return ("ok",)
        if cls in ("south_only", "north_only"):
            return ("warn",)
        if cls == "none":
            return ("low",)
        return ("alt",) if index % 2 == 1 else ()

    def _island_status_text(self, item):
        if item.get("ready_not_displayed") and not item.get("exempted"):
            return "★ 双有未陈列"
        if item.get("gap"):
            return "★ 有货未展示"
        if item.get("warehouse_only") and not item.get("exempted"):
            return "仓有·店仓无"
        if item.get("exempted"):
            return "○ 同组已展示"
        if item.get("discontinued"):
            return "已停产"
        if item.get("in_stock"):
            return "有货"
        return "无货"

    def _render_island_quadrants(self, store_specific=True):
        if not self._island_tree:
            return
        region = self._cached_summary.get("region") or self._current_region()
        self._reload_owner_config(region)
        # 刷新时勿 reset 渠道，否则用户刚选的 130/830 会被打回「全部渠道」
        self._update_island_owner_channel_combos(reset_channel=False)
        supported = bool(self._cached_summary.get("island_stock_supported"))
        if self._island_unsupported_lbl:
            if supported:
                self._island_unsupported_lbl.configure(text="")
            else:
                self._island_unsupported_lbl.configure(
                    text=f"当前地区 {region} 暂无南北岛划分（仅 NZ 支持）。",
                )
        view_mode = self._island_view_mode_var.get()
        if self._island_owner_combo:
            self._island_owner_combo.configure(
                state="readonly" if view_mode == "总览" else "disabled",
            )
        if view_mode == "总览":
            self._update_island_channel_combo(reset=False)
        elif self._island_channel_combo:
            self._island_channel_combo.configure(state="disabled")
        if view_mode == "总览":
            if self._island_single_frame:
                self._island_single_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
            if self._island_groups_outer:
                self._island_groups_outer.pack_forget()
            owner, channel = self._island_scope_filters()
            report = panel_data.aggregate_island_quadrants(
                self._cached_products,
                owner=owner, channel=channel,
                config_rows=self._cached_owner_config,
            )
            self._update_main_island_quadrant(report)
            self._apply_island_quadrant_styles()
            if self._island_status_lbl:
                scope = []
                if owner:
                    scope.append(owner)
                if channel:
                    scope.append(f"渠道 {channel}")
                scope_text = " · ".join(scope) if scope else "全部 SKU"
                self._island_status_lbl.configure(
                    text=f"总览 · {scope_text} · 共 {report.get('total', 0)} 条在产 SKU",
                )
        else:
            if self._island_single_frame:
                self._island_single_frame.pack_forget()
            if self._island_groups_outer:
                self._island_groups_outer.pack(fill=tk.BOTH, expand=False, padx=8, pady=(0, 8))
            self._render_island_group_quadrants(store_specific)
        self._render_island_table(store_specific)

    def _render_island_table(self, store_specific=True):
        if not self._island_tree:
            return
        if not self._cached_summary.get("island_stock_supported"):
            if self._island_tree.get_children():
                self._island_tree.delete(*self._island_tree.get_children())
            if self._island_status_lbl and self._island_view_mode_var.get() == "总览":
                self._island_status_lbl.configure(text="")
            return
        owner, channel = self._island_scope_filters()
        report = panel_data.aggregate_island_quadrants(
            self._cached_products,
            owner=owner, channel=channel,
            config_rows=self._cached_owner_config,
        )
        rows = []
        if self._island_selected_class:
            rows = report["rows"].get(self._island_selected_class, [])
        else:
            for class_key in panel_data.ISLAND_STOCK_CLASSES:
                rows.extend(report["rows"].get(class_key, []))
        rows.sort(key=lambda p: (
            p.get("island_stock_class") or "",
            not p.get("in_stock"),
            -(p.get("north_stock_qty") or 0) - (p.get("south_stock_qty") or 0),
            p.get("code") or "",
        ))
        if self._island_tree.get_children():
            self._island_tree.delete(*self._island_tree.get_children())
        for idx, item in enumerate(rows):
            stock = int(item.get("stock_qty") or 0) if item.get("in_stock") else 0
            owner_name, _cfg_channel = panel_data.resolve_product_owner_channel(
                item, self._cached_owner_config,
            )
            channel_label = panel_data.sku_prefix(item.get("code", "")) or _cfg_channel or "-"
            self._island_tree.insert(
                "", tk.END,
                values=(
                    item.get("code") or "",
                    item.get("name") or "",
                    owner_name or "-",
                    channel_label,
                    item.get("north_stock_qty", 0),
                    item.get("south_stock_qty", 0),
                    item.get("island_stock_label") or "-",
                    stock if store_specific else "-",
                    self._island_status_text(item),
                ),
                tags=self._island_row_tag(item, idx),
            )
        if self._island_status_lbl and self._island_view_mode_var.get() == "总览":
            label = (
                panel_data.ISLAND_STOCK_CLASSES[self._island_selected_class]
                if self._island_selected_class else "全部象限"
            )
            scope_bits = []
            if owner:
                scope_bits.append(owner)
            if channel:
                scope_bits.append(f"渠道 {channel}")
            scope_text = " · ".join(scope_bits) if scope_bits else "全部 SKU"
            self._island_status_lbl.configure(
                text=f"{label} · {scope_text} · 显示 {len(rows)} 条在产 SKU",
            )

    def _scroll_island(self, direction):
        if self._island_tree:
            self._island_tree.yview_scroll(direction * SCROLL_UNITS, "units")

    def _on_island_wheel(self, event):
        step = -1 if event.delta > 0 else 1
        widget = event.widget
        if widget is self._island_groups_canvas and self._island_groups_canvas:
            self._island_groups_canvas.yview_scroll(step * SCROLL_UNITS, "units")
        else:
            self._scroll_island(step)

    def _on_island_double_click(self, _event=None):
        sel = self._island_tree.selection() if self._island_tree else ()
        if not sel:
            return
        values = self._island_tree.item(sel[0], "values")
        if not values:
            return
        self.search_var.set(str(values[0]))
        if self._notebook:
            self._notebook.select(self._tab_products)
        self._refresh_view()

    def _reload_owner_config(self, region=None, data_dir=None):
        region = region or self._cached_summary.get("region") or self._current_region()
        data_dir = data_dir or self._cached_data_dir
        owner_cfg, owner_path = panel_data.load_channel_owner_config(region, data_dir)
        self._cached_owner_config = owner_cfg
        self._cached_owner_path = owner_path or panel_data.resolve_channel_owner_path(region, data_dir) or ""

    def _on_owner_filter_change(self):
        self._owner_detail_filter = None
        self._render_owner_table(self._cached_summary.get("store_specific", self._is_store_selected()))

    def _on_owner_view_change(self):
        if self._owner_view_var.get() != "分栏视图":
            self._owner_detail_filter = None
        self._render_owner_table(self._cached_summary.get("store_specific", self._is_store_selected()))

    def _owner_summary_headings(self):
        return {
            "owner": ("负责人", 88), "channel": ("渠道数", 64), "lead_time": ("LeadTime", 72),
            "merge": ("合并计算", 120), "sku": ("SKU数", 64), "units": ("统计单位", 72),
            "in_stock": ("有货单位", 72), "in_stock_rate": ("有货率", 72),
            "gap": ("有货未展示", 88), "exempted": ("同组豁免", 72), "note": ("备注", 160),
        }

    def _owner_channel_headings(self):
        return {
            "owner": ("负责人", 72), "channel": ("渠道", 72), "lead_time": ("LeadTime", 72),
            "merge": ("合并计算", 120), "sku": ("SKU数", 64), "units": ("统计单位", 72),
            "in_stock": ("有货单位", 72), "in_stock_rate": ("有货率", 72),
            "gap": ("有货未展示", 88), "exempted": ("同组豁免", 72), "note": ("备注", 160),
        }

    def _apply_owner_headings(self, tree, headings):
        if not tree:
            return
        for col, (text, width) in headings.items():
            tree.heading(col, text=text)
            tree.column(
                col, width=width, anchor="center" if col not in ("owner", "merge", "note") else "w",
            )

    def _owner_pct(self, value):
        return "-" if value is None else f"{value:.1f}%"

    def _update_owner_pane_layout(self, view):
        if not self._owner_paned:
            return
        panes = self._owner_paned.panes()
        show_summary = view in ("分栏视图", "负责人汇总")
        show_detail = view in ("分栏视图", "渠道明细")
        for frame in (self._owner_summary_frame, self._owner_detail_frame):
            if frame in panes:
                self._owner_paned.forget(frame)
        if show_summary:
            self._owner_paned.add(self._owner_summary_frame, minsize=120)
        if show_detail:
            self._owner_paned.add(self._owner_detail_frame, minsize=160)

    def _render_owner_channel_table(self, rows, store_specific=True):
        if not self._owner_channel_tree:
            return
        self._apply_owner_headings(self._owner_channel_tree, self._owner_channel_headings())
        if self._owner_channel_tree.get_children():
            self._owner_channel_tree.delete(*self._owner_channel_tree.get_children())
        for idx, row in enumerate(rows):
            gap_val = row.get("gap_count") if store_specific else "-"
            exempt_val = row.get("exempted_count") if store_specific else "-"
            values = (
                row["owner"], row["channel"], row.get("lead_time") or "-",
                row.get("merge_products") or "所有", row["sku_count"],
                row["total_units"], row["in_stock_units"], self._owner_pct(row["in_stock_rate"]),
                gap_val, exempt_val, row.get("note") or "",
            )
            self._owner_channel_tree.insert(
                "", tk.END, values=values,
                tags=self._owner_rate_tag(row, idx),
            )

    def _filtered_owner_channels(self, report, owner_filter=None, selected_owner=None):
        rows = report["channels"]
        if owner_filter and owner_filter != "全部负责人":
            rows = [r for r in rows if r["owner"] == owner_filter]
        if selected_owner:
            rows = [r for r in rows if r["owner"] == selected_owner]
        return rows

    def _render_owner_table(self, store_specific=True):
        if not self._owner_tree:
            return
        region = self._cached_summary.get("region") or self._current_region()
        self._reload_owner_config(region)
        expected = panel_data.expected_channel_owner_path(region)
        if not self._cached_owner_config:
            if self._cached_owner_path:
                fname = Path(self._cached_owner_path).name
                self._owner_status_lbl.configure(
                    text=(
                        f"已找到 {fname}，但未能读取负责人/渠道数据（{self._cached_owner_path}）。"
                        f"请确认 A列=负责人、B列=渠道（可无表头），Excel 请另存为 CSV UTF-8 后点「刷新数据」"
                    ),
                )
            else:
                self._owner_status_lbl.configure(
                    text=(
                        f"未找到 channel_owner.csv / channel_owners.csv，"
                        f"请放到 {expected.parent}/（文件名二选一，支持仅两列：负责人+渠道）"
                    ),
                )
            if self._owner_tree.get_children():
                self._owner_tree.delete(*self._owner_tree.get_children())
            if self._owner_channel_tree and self._owner_channel_tree.get_children():
                self._owner_channel_tree.delete(*self._owner_channel_tree.get_children())
            self._cached_owner_report = None
            return

        report = panel_data.aggregate_by_channel_owner(
            self._cached_products,
            region=self._cached_summary.get("region"),
            store_specific=store_specific,
            config_rows=self._cached_owner_config,
        )
        self._cached_owner_report = report
        owners = ["全部负责人"] + sorted({r["owner"] for r in report["owners"]})
        self._owner_filter_combo.configure(values=owners)
        if self._owner_filter_var.get() not in owners:
            self._owner_filter_var.set("全部负责人")

        view = self._owner_view_var.get()
        if view not in ("分栏视图", "负责人汇总", "渠道明细"):
            view = "分栏视图"
            self._owner_view_var.set(view)
        owner_filter = self._owner_filter_var.get()
        self._update_owner_pane_layout(view)

        if view in ("分栏视图", "负责人汇总"):
            rows = report["owners"]
            if owner_filter != "全部负责人":
                rows = [r for r in rows if r["owner"] == owner_filter]
            self._apply_owner_headings(self._owner_tree, self._owner_summary_headings())
            if self._owner_tree.get_children():
                self._owner_tree.delete(*self._owner_tree.get_children())
            for idx, row in enumerate(rows):
                gap_val = row.get("gap_count") if store_specific else "-"
                exempt_val = row.get("exempted_count") if store_specific else "-"
                values = (
                    row["owner"], row["channel_count"], "-", "-",
                    row["sku_count"], row["total_units"], row["in_stock_units"],
                    self._owner_pct(row["in_stock_rate"]), gap_val, exempt_val, "",
                )
                self._owner_tree.insert(
                    "", tk.END, values=values,
                    tags=self._owner_rate_tag(row, idx),
                )
        elif self._owner_tree.get_children():
            self._owner_tree.delete(*self._owner_tree.get_children())

        if view in ("分栏视图", "渠道明细"):
            selected_owner = self._owner_detail_filter if view == "分栏视图" else None
            channel_rows = self._filtered_owner_channels(report, owner_filter, selected_owner)
            self._render_owner_channel_table(channel_rows, store_specific)
        elif self._owner_channel_tree and self._owner_channel_tree.get_children():
            self._owner_channel_tree.delete(*self._owner_channel_tree.get_children())

        fname = Path(self._cached_owner_path).name if self._cached_owner_path else "channel_owners.csv"
        detail_count = len(self._filtered_owner_channels(
            report, owner_filter, self._owner_detail_filter if view == "分栏视图" else None,
        )) if view in ("分栏视图", "渠道明细") else len(report["channels"])
        self._owner_status_lbl.configure(
            text=(
                f"配置：{fname} · {len(report['owners'])} 位负责人 · "
                f"{len(report['channels'])} 条渠道规则 · 当前明细 {detail_count} 条"
            ),
        )

    def _current_notebook_tab(self):
        if not self._notebook:
            return ""
        try:
            return str(self._notebook.tab(self._notebook.select(), "text") or "")
        except Exception:
            return ""

    def _ask_save_csv(self, default_name):
        if filedialog is None:
            if messagebox:
                messagebox.showerror("导出", "当前环境没有文件保存对话框。")
            return ""
        initial = Path(self._cached_data_dir or ".")
        if not initial.is_dir():
            initial = Path.home()
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出当前筛选",
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            initialdir=str(initial),
            initialfile=default_name,
        )
        return str(path or "").strip()

    def _export_default_stamp(self):
        region = self._cached_summary.get("region") or self._current_region() or "NZ"
        store = self._cached_summary.get("store") or self.store_var.get() or "store"
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        return panel_data.export_filename_slug(region), panel_data.export_filename_slug(store), stamp

    def _export_current_filtered(self):
        tab = self._current_notebook_tab()
        if tab == "On Hold 分析":
            self._export_filtered_onhold()
            return
        if tab in ("", "产品明细"):
            self._export_filtered_products()
            return
        if messagebox:
            messagebox.showinfo(
                "导出当前筛选",
                f"「{tab}」页请用该页自己的导出（负责人报表有「导出 CSV 报表」）。\n"
                "此按钮导出「产品明细」或「On Hold 分析」当前筛选结果。",
            )

    def _collect_filtered_onhold_rows(self):
        region = self._cached_summary.get("region") or self._current_region()
        try:
            bundle = panel_data.get_region_bundle(region)
        except Exception:
            bundle = {}
        bl = bundle.get("blacklist") or set()
        status_f = self._onhold_status_filter_value()
        days_f = self._onhold_days_filter_value()
        min_days = panel_data.parse_on_hold_min_days(days_f)
        rows, _total = panel_data.list_on_hold_analysis(
            bundle,
            status_filter=status_f,
            catalog_by_norm=self._catalog_by_norm(),
            min_hold_days=min_days,
            blacklist=bl,
            max_rows=0,
        )
        try:
            rows = self._sort_onhold_rows(rows)
        except Exception:
            pass
        return rows, status_f, days_f, min_days

    def _export_filtered_onhold(self):
        rows, status_f, days_f, min_days = self._collect_filtered_onhold_rows()
        if not rows:
            if messagebox:
                messagebox.showinfo("导出当前筛选", "当前筛选没有可导出的 On Hold 行。")
            return
        region, _store, stamp = self._export_default_stamp()
        status_slug = panel_data.export_filename_slug(status_f, "allstatus")
        days_slug = f"{min_days}d" if min_days else "alldays"
        default_name = f"onhold_{region}_{status_slug}_{days_slug}_{stamp}.csv"
        path = self._ask_save_csv(default_name)
        if not path:
            return
        export_rows = [panel_data.format_onhold_export_row(row) for row in rows]
        headers = [panel_data.ONHOLD_EXPORT_HEADERS[k] for k in panel_data.ONHOLD_EXPORT_FIELDS]
        named_rows = [
            {panel_data.ONHOLD_EXPORT_HEADERS[k]: row[k] for k in panel_data.ONHOLD_EXPORT_FIELDS}
            for row in export_rows
        ]
        out = panel_data.write_export_csv(path, headers, named_rows)
        hint = f"已导出 {len(export_rows)} 条当前筛选"
        if status_f and status_f != "全部状态":
            hint += f" · {status_f}"
        if min_days:
            hint += f" · 冻结≥{min_days}天"
        hint += f"：{out}"
        if self._onhold_status_lbl:
            self._onhold_status_lbl.configure(text=hint)
        self._status_var.set(hint)
        if messagebox:
            messagebox.showinfo("导出当前筛选", hint)

    def _export_filtered_products(self):
        if not self._cached_products:
            if messagebox:
                messagebox.showinfo("导出当前筛选", "还没有产品数据。请先点「刷新数据」。")
            return
        filtered = self._sort_products(self._apply_client_filters(self._cached_products))
        if not filtered:
            if messagebox:
                messagebox.showinfo("导出当前筛选", "当前筛选没有可导出的产品。")
            return
        region, store, stamp = self._export_default_stamp()
        default_name = f"products_{region}_{store}_{stamp}.csv"
        path = self._ask_save_csv(default_name)
        if not path:
            return
        named_rows = []
        for item in filtered:
            values = self._tree_row_values(item)
            named_rows.append({
                "编码": values[0],
                "名称": values[1],
                "系列": values[2],
                "价格": values[3],
                "库存": values[4],
                "南北岛": values[5],
                "展示": values[6],
                "停产": values[7],
                "状态": values[8],
            })
        headers = ["编码", "名称", "系列", "价格", "库存", "南北岛", "展示", "停产", "状态"]
        out = panel_data.write_export_csv(path, headers, named_rows)
        hint = f"已导出 {len(named_rows)} 条当前筛选：{out}"
        self._status_var.set(hint)
        if self.result_count_var:
            self.result_count_var.set(f"{self.result_count_var.get()} · 已导出 {len(named_rows)} 条")
        if messagebox:
            messagebox.showinfo("导出当前筛选", hint)

    def _export_owner_report(self):
        if not self._cached_products:
            return
        store_specific = self._cached_summary.get("store_specific", self._is_store_selected())
        report = panel_data.aggregate_by_channel_owner(
            self._cached_products,
            region=self._cached_summary.get("region"),
            store_specific=store_specific,
            config_rows=self._cached_owner_config,
        )
        if not report["channels"]:
            return
        region = self._cached_summary.get("region", "NZ")
        store = self._cached_summary.get("store", panel_data.ALL_STORES)
        out_dir = Path(self._cached_data_dir or panel_data.expected_channel_owner_path(region).parent)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        out_path = out_dir / f"owner_report_{region}_{stamp}.csv"
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "region", "store", "owner", "channel", "lead_time", "merge_products",
                "merge_regions", "sku_count", "total_units", "in_stock_units",
                "in_stock_rate", "gap_count", "exempted_count", "note",
            ])
            for row in report["channels"]:
                writer.writerow([
                    region, store, row["owner"], row["channel"], row.get("lead_time") or "",
                    row.get("merge_products") or "", row.get("merge_regions") or "",
                    row["sku_count"], row["total_units"], row["in_stock_units"],
                    row.get("in_stock_rate") if row.get("in_stock_rate") is not None else "",
                    row.get("gap_count") if row.get("gap_count") is not None else "",
                    row.get("exempted_count") if row.get("exempted_count") is not None else "",
                    row.get("note") or "",
                ])
            writer.writerow([])
            writer.writerow([
                "region", "store", "owner", "channel_count", "sku_count",
                "total_units", "in_stock_units", "in_stock_rate", "gap_count", "exempted_count",
            ])
            for row in report["owners"]:
                writer.writerow([
                    region, store, row["owner"], row["channel_count"], row["sku_count"],
                    row["total_units"], row["in_stock_units"],
                    row.get("in_stock_rate") if row.get("in_stock_rate") is not None else "",
                    row.get("gap_count") if row.get("gap_count") is not None else "",
                    row.get("exempted_count") if row.get("exempted_count") is not None else "",
                ])
        self._owner_status_lbl.configure(text=f"已导出：{out_path}")

    def _scroll_owner(self, direction, widget=None):
        target = widget
        if target is None:
            target = self._owner_tree
        if target:
            target.yview_scroll(direction * SCROLL_UNITS, "units")

    def _on_owner_wheel(self, event):
        step = -1 if event.delta > 0 else 1
        widget = event.widget
        if widget is self._owner_channel_tree:
            self._scroll_owner(step, self._owner_channel_tree)
        else:
            self._scroll_owner(step, self._owner_tree)

    def _on_owner_select(self, _event=None):
        if self._owner_view_var.get() != "分栏视图" or not self._owner_tree:
            return
        sel = self._owner_tree.selection()
        if not sel:
            self._owner_detail_filter = None
        else:
            values = self._owner_tree.item(sel[0], "values")
            self._owner_detail_filter = str(values[0]) if values else None
        if not self._cached_owner_report:
            return
        store_specific = self._cached_summary.get("store_specific", self._is_store_selected())
        owner_filter = self._owner_filter_var.get()
        channel_rows = self._filtered_owner_channels(
            self._cached_owner_report, owner_filter, self._owner_detail_filter,
        )
        self._render_owner_channel_table(channel_rows, store_specific)
        report = self._cached_owner_report
        fname = Path(self._cached_owner_path).name if self._cached_owner_path else "channel_owners.csv"
        self._owner_status_lbl.configure(
            text=(
                f"配置：{fname} · {len(report['owners'])} 位负责人 · "
                f"{len(report['channels'])} 条渠道规则 · 当前明细 {len(channel_rows)} 条"
            ),
        )

    def _on_owner_summary_double_click(self, _event=None):
        sel = self._owner_tree.selection() if self._owner_tree else ()
        if not sel:
            return
        values = self._owner_tree.item(sel[0], "values")
        if not values:
            return
        owner = str(values[0])
        if self._owner_view_var.get() == "负责人汇总":
            self._owner_filter_var.set(owner)
            self._owner_view_var.set("渠道明细")
            self._owner_detail_filter = None
            self._render_owner_table(self._cached_summary.get("store_specific", self._is_store_selected()))
            return
        self._owner_detail_filter = owner
        self._on_owner_select()

    def _on_owner_channel_double_click(self, _event=None):
        sel = self._owner_channel_tree.selection() if self._owner_channel_tree else ()
        if not sel:
            return
        values = self._owner_channel_tree.item(sel[0], "values")
        if not values:
            return
        channel = str(values[1])
        if channel and channel != "-":
            self.search_var.set(channel)
            if self._notebook:
                self._notebook.select(self._tab_products)
            self._refresh_view()

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
        if item.get("ready_not_displayed"):
            return ("gap",)
        if item.get("gap"):
            return ("gap",)
        if item.get("warehouse_only") and not item.get("exempted"):
            return ("warehouse_only",)
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
        if item.get("displayed"):
            displayed = "已展示"
        elif item.get("in_storage"):
            displayed = f"未展示·店仓{item.get('storage_qty', 0):.0f}"
        else:
            displayed = "未展示"
        discontinue = "是" if item.get("discontinued") else "否"
        if item.get("on_hold"):
            status = f"⏸ On Hold {int(item.get('on_hold_qty') or 0)}"
        elif item.get("is_part"):
            status = f"配件 {int(item.get('parts_qty') or 0)}"
        elif item.get("ready_not_displayed") and not item.get("exempted"):
            status = "★ 双有未陈列"
        elif item.get("gap"):
            status = "★ 有货未展示"
        elif item.get("warehouse_only") and not item.get("exempted"):
            status = "仓有·店仓无"
        elif item.get("exempted"):
            status = "○ 同组已展示"
        elif item.get("discontinued"):
            if item.get("in_stock") and not item.get("displayed"):
                status = "有货未展示(停产)"
            else:
                status = "已停产"
        elif item.get("in_stock"):
            if item.get("in_storage"):
                status = "有货·店仓有"
            else:
                status = "有货"
        else:
            status = "无货"
        island = item.get("island_stock_label") or "-"
        return (
            item["code"], item.get("name") or "", item.get("family") or "",
            price, stock, island, displayed, discontinue, status,
        )

    def _insert_group_children(self, parent, items, render_token, start=0):
        if render_token != self._render_token:
            return
        end = min(start + 50, len(items))
        for idx in range(start, end):
            item = items[idx]
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
            if self._images_enabled() and url:
                self._schedule_row_image(iid, url, render_token)
        if end < len(items):
            self.root.after(1, lambda: self._insert_group_children(
                parent, items, render_token, end,
            ))
        else:
            self._debounce_visible_images()

    def _update_group_tool_buttons(self):
        if not getattr(self, "_expand_all_btn", None):
            return
        has_lazy = bool(self._lazy_groups)
        has_open = False
        if self._tree:
            for iid in self._tree.get_children():
                if self._tree.get_children(iid):
                    has_open = True
                    break
        self._expand_all_btn.configure(state=tk.NORMAL if has_lazy else tk.DISABLED)
        self._collapse_all_btn.configure(state=tk.NORMAL if has_open else tk.DISABLED)

    def _expand_all_groups(self):
        if not self._tree:
            return
        self._sync_filter_combos()
        pending = [iid for iid in self._tree.get_children() if iid in self._lazy_groups]
        if not pending:
            return
        self._status_var.set(f"正在展开 {len(pending)} 个系列…")
        total = len(pending)

        def step(ix=0):
            if ix >= total:
                self._update_group_tool_buttons()
                self._debounce_visible_images()
                self._status_var.set(f"就绪 · 已展开 {total} 个系列")
                return
            batch = pending[ix:ix + 6]
            for iid in batch:
                if iid not in self._lazy_groups:
                    continue
                label = self._group_labels.get(iid, "")
                vals = list(self._tree.item(iid, "values"))
                if len(vals) > 1 and label:
                    vals[1] = label
                    self._tree.item(iid, values=vals)
                self._tree.item(iid, open=True)
                self._populate_lazy_group(iid)
            self.root.after(8, lambda: step(ix + len(batch)))

        step(0)

    def _collapse_all_groups(self):
        if not self._tree:
            return
        render_token = self._render_token
        folded = 0
        for iid in self._tree.get_children():
            children = self._tree.get_children(iid)
            if not children:
                continue
            items = [self._products_by_iid[c] for c in children if c in self._products_by_iid]
            for c in children:
                self._products_by_iid.pop(c, None)
                self._iid_to_url.pop(c, None)
            self._tree.delete(*children)
            self._lazy_groups[iid] = (items, render_token)
            label = self._group_labels.get(iid, "")
            vals = list(self._tree.item(iid, "values"))
            if len(vals) > 1:
                vals[1] = f"▸ {label}" if label else vals[1]
                self._tree.item(iid, values=vals, open=False)
            folded += 1
        self._update_group_tool_buttons()
        if folded:
            self._status_var.set(f"就绪 · 已折叠 {folded} 个系列")

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
        self._group_labels.clear()
        if self._tree.get_children():
            self._tree.delete(*self._tree.get_children())
        self._products_by_iid.clear()
        self._iid_to_url.clear()
        self._pending_urls.clear()
        self._loading_urls.clear()

        grouped = self._group_products(products)
        store_specific = self._cached_summary.get(
            "store_specific", self._is_store_selected()
        )
        expand_all = len(products) <= AUTO_EXPAND_ALL_GROUPS

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
            # 大结果集（如三个「全部」=16077 条）：全部折叠，避免单 SKU 系列自动展开刷屏
            expand_now = expand_all
            eager_children = expand_now and len(items) <= MAX_EXPAND_GROUP_ITEMS
            show_open = expand_now and eager_children
            label_text = f"{'▸ ' if not show_open else ''}{family_label} {summary}"
            full_label = f"{family_label} {summary}"
            parent = self._tree.insert(
                "", tk.END, text="",
                values=("", label_text, "", "", "", "", "", ""),
                tags=("group",), open=show_open,
            )
            self._group_labels[parent] = full_label
            if eager_children:
                self._insert_group_children(parent, items, render_token)
            else:
                self._lazy_groups[parent] = (items, render_token)
            return parent

        def fill_batch(start=0):
            if render_token != self._render_token:
                return
            end = min(start + 60, len(grouped))
            for family_label, items in grouped[start:end]:
                insert_group(family_label, items)
            if end < len(grouped):
                self.root.after(1, lambda s=end: fill_batch(s))
            else:
                self._update_group_tool_buttons()
                self._load_visible_images()

        if grouped:
            self.root.after_idle(lambda: fill_batch(0))
        else:
            self._update_group_tool_buttons()
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
