"""库存健康看板 UI 片段（供 panel_app 嵌入）。"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk

import inventory_health as ih
import inventory_health_chart as ihc


def attach_inventory_health_tab(app, notebook, style_colors: dict):
    """在 notebook 上添加「库存健康」标签页。app 需有 region_var、log 方法（可选）。"""
    tab = ttk.Frame(notebook)
    notebook.add(tab, text="库存健康")

    app._tab_inventory_health = tab
    app._ih_report = None
    app._ih_chart_frame = None
    app._ih_chart_widget = None

    app._ih_channel_var = tk.StringVar(value="")
    app._ih_category_var = tk.StringVar(value="")
    app._ih_sku_var = tk.StringVar(value="")
    app._ih_branch_var = tk.StringVar(value="")
    app._ih_group_var = tk.StringVar(value="sku")
    app._ih_stockout_th = tk.StringVar(value=str(ih.DEFAULT_STOCKOUT_X))
    app._ih_days_th = tk.StringVar(value=str(ih.DEFAULT_DAYS_Y))
    app._ih_cover_proxy = tk.StringVar(value=str(ih.DEFAULT_COVER_DAYS_PROXY))

    toolbar = tk.Frame(tab, bg="white")
    toolbar.pack(fill=tk.X, padx=6, pady=6)
    ttk.Button(toolbar, text="刷新", command=lambda: app._render_inventory_health(force=True)).pack(side=tk.LEFT, padx=(0, 8))
    ttk.Label(toolbar, text="渠道").pack(side=tk.LEFT)
    ttk.Entry(toolbar, width=8, textvariable=app._ih_channel_var).pack(side=tk.LEFT, padx=4)
    ttk.Label(toolbar, text="分类").pack(side=tk.LEFT)
    ttk.Entry(toolbar, width=10, textvariable=app._ih_category_var).pack(side=tk.LEFT, padx=4)
    ttk.Label(toolbar, text="SKU").pack(side=tk.LEFT)
    ttk.Entry(toolbar, width=12, textvariable=app._ih_sku_var).pack(side=tk.LEFT, padx=4)
    ttk.Label(toolbar, text="分店").pack(side=tk.LEFT)
    ttk.Entry(toolbar, width=10, textvariable=app._ih_branch_var).pack(side=tk.LEFT, padx=4)
    ttk.Label(toolbar, text="汇总").pack(side=tk.LEFT, padx=(8, 0))
    ttk.Combobox(
        toolbar, width=10, state="readonly", textvariable=app._ih_group_var,
        values=("sku", "channel", "category"),
    ).pack(side=tk.LEFT, padx=4)
    ttk.Label(toolbar, text="缺货线%").pack(side=tk.LEFT, padx=(8, 0))
    ttk.Entry(toolbar, width=5, textvariable=app._ih_stockout_th).pack(side=tk.LEFT, padx=2)
    ttk.Label(toolbar, text="天数线").pack(side=tk.LEFT)
    ttk.Entry(toolbar, width=5, textvariable=app._ih_days_th).pack(side=tk.LEFT, padx=2)
    ttk.Label(toolbar, text="覆盖代理天").pack(side=tk.LEFT)
    ttk.Entry(toolbar, width=5, textvariable=app._ih_cover_proxy).pack(side=tk.LEFT, padx=2)

    app._ih_status = tk.Label(tab, text="", bg="white", fg=style_colors.get("muted", "#64748b"), font=("Segoe UI", 9))
    app._ih_status.pack(anchor="w", padx=8)

    cards = tk.Frame(tab, bg="white")
    cards.pack(fill=tk.X, padx=6, pady=4)
    app._ih_card_labels = {}
    for key, title in (
        ("inv_m3", "在库体积 m³"),
        ("demand_m3", "日均需求 m³/天"),
        ("days", "理论库存天数"),
        ("stockout", "平均缺货率%"),
        ("high_so", "高缺货 SKU"),
        ("mismatch", "错配象限 SKU"),
        ("value", "库存金额"),
    ):
        f = tk.Frame(cards, bg="#f1f5f9", padx=8, pady=6)
        f.pack(side=tk.LEFT, padx=4, fill=tk.Y)
        tk.Label(f, text=title, bg="#f1f5f9", fg="#64748b", font=("Segoe UI", 8)).pack(anchor="w")
        val = tk.Label(f, text="—", bg="#f1f5f9", fg="#1e293b", font=("Segoe UI", 11, "bold"))
        val.pack(anchor="w")
        app._ih_card_labels[key] = val

    panes = tk.PanedWindow(tab, orient=tk.HORIZONTAL, bg="white", sashwidth=6)
    panes.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
    chart_frame = tk.Frame(panes, bg="white")
    table_frame = tk.Frame(panes, bg="white")
    panes.add(chart_frame, minsize=380)
    panes.add(table_frame, minsize=320)
    app._ih_chart_frame = chart_frame

    cols = (
        "priority", "sku", "channel", "demand_src", "stockout", "days", "demand_m3",
        "inv_m3", "transit", "quadrant",
    )
    app._ih_tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=16)
    headings = {
        "priority": ("P", 36), "sku": ("SKU", 88), "channel": ("渠道", 48),
        "demand_src": ("需求来源", 72), "stockout": ("缺货%", 56), "days": ("库存天", 56),
        "demand_m3": ("m³/天", 64),
        "inv_m3": ("在库m³", 64), "transit": ("在途m³", 64), "quadrant": ("象限", 120),
    }
    for c, (t, w) in headings.items():
        app._ih_tree.heading(c, text=t)
        app._ih_tree.column(c, width=w, anchor="center" if c != "sku" else "w")
    scroll = ttk.Scrollbar(table_frame, orient="vertical", command=app._ih_tree.yview)
    app._ih_tree.configure(yscrollcommand=scroll.set)
    app._ih_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    app._ih_tree.tag_configure("mismatch", background="#fee2e2")

    formula = tk.Label(
        tab,
        text="日均需求体积 = 日均件数 × 单件体积；理论库存天数 = 在库体积 ÷ 日均需求体积",
        bg="white", fg="#64748b", font=("Segoe UI", 8),
        wraplength=900, justify=tk.LEFT,
    )
    formula.pack(anchor="w", padx=8, pady=(0, 6))


def render_inventory_health(app, force=False):
    region = app._current_region() if hasattr(app, "_current_region") else "NZ"

    MAX_TABLE_ROWS = 600

    def work():
        try:
            t0 = time.perf_counter()
            th = ih.HealthThresholds(
                stockout_pct=float(app._ih_stockout_th.get() or ih.DEFAULT_STOCKOUT_X),
                consumption_days=float(app._ih_days_th.get() or ih.DEFAULT_DAYS_Y),
                cover_days_proxy=float(app._ih_cover_proxy.get() or ih.DEFAULT_COVER_DAYS_PROXY),
            )
            report = ih.build_inventory_health_report(
                region,
                channel=app._ih_channel_var.get(),
                category=app._ih_category_var.get(),
                sku_filter=app._ih_sku_var.get(),
                branch=app._ih_branch_var.get(),
                group_by=app._ih_group_var.get() or "sku",
                thresholds=th,
            )
            elapsed = time.perf_counter() - t0
            app.after(0, lambda: _apply_report(app, report, th, elapsed, MAX_TABLE_ROWS))
        except Exception as exc:
            app.after(0, lambda: app._ih_status.configure(text=f"库存健康加载失败：{exc}"))

    if force or not getattr(app, "_ih_report", None):
        app._ih_status.configure(
            text="正在计算库存健康指标…（首次会读 stock + sales + weekly_sales，约几秒～半分钟）",
        )
        threading.Thread(target=work, daemon=True).start()


def _apply_report(app, report, th, elapsed_sec=0.0, max_table_rows=600):
    app._ih_report = report
    s = report.get("summary") or {}
    cards = app._ih_card_labels
    cards["inv_m3"].configure(text=str(s.get("total_inventory_volume_m3", "—")))
    cards["demand_m3"].configure(text=str(s.get("total_avg_daily_demand_m3", "—")))
    cards["days"].configure(text=str(s.get("overall_theoretical_days", "—")))
    cards["stockout"].configure(text=str(s.get("overall_stockout_rate_pct", "—")))
    cards["high_so"].configure(text=str(s.get("high_stockout_sku_count", "—")))
    cards["mismatch"].configure(text=str(s.get("inventory_mismatch_count", "—")))
    cards["value"].configure(text=str(s.get("inventory_value") or "—"))

    meta = report.get("meta") or {}
    warns = "; ".join(report.get("warnings") or [])
    rows_sorted = sorted(
        report.get("rows") or [],
        key=lambda r: (r.get("priority", 9), -(r.get("bubble_m3_day") or 0)),
    )
    total_rows = len(rows_sorted)
    display_rows = rows_sorted[: max(1, int(max_table_rows or 600))]
    timing = f" · 计算 {elapsed_sec:.1f}s" if elapsed_sec else ""
    trunc = f" · 表格 {len(display_rows)}/{total_rows}" if total_rows > len(display_rows) else ""
    app._ih_status.configure(
        text=f"{report.get('region')} · {s.get('sku_count', 0)} 点{timing}{trunc} · "
        f"销量跨度 {meta.get('sales_span_days')} 天 · {warns or meta.get('stockout_note', '')[:100]}",
    )

    for iid in app._ih_tree.get_children():
        app._ih_tree.delete(iid)
    for row in display_rows:
        tag = ("mismatch",) if row.get("quadrant") == "Inventory Mismatch" else ()
        app._ih_tree.insert(
            "", "end",
            values=(
                row.get("priority"),
                row.get("sku"),
                row.get("channel"),
                row.get("demand_source"),
                row.get("stockout_rate_pct"),
                row.get("theoretical_days_label"),
                row.get("avg_daily_demand_m3"),
                row.get("inventory_volume_m3"),
                row.get("transit_volume_m3"),
                row.get("quadrant"),
            ),
            tags=tag,
        )

    if app._ih_chart_widget:
        app._ih_chart_widget.destroy()
    for w in app._ih_chart_frame.winfo_children():
        w.destroy()
    app._ih_chart_widget, _fig = ihc.render_bubble_chart(
        app._ih_chart_frame, report, th.__dict__,
    )
