"""Matplotlib 库存健康气泡图（嵌入 Tkinter）。"""

from __future__ import annotations

import os
from typing import Any

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    FigureCanvasTkAgg = None
    Figure = None


QUADRANT_LABELS = {
    "tl": "Potential Overstock",
    "tr": "Inventory Mismatch",
    "bl": "Healthy",
    "br": "Supply Shortage",
}

# 个别 SKU 库存天数极大（低销高库存）会把 Y 轴拉到几千天，其余点全贴在 X 轴下。
_Y_CAP_PERCENTILE = float(os.getenv("INVENTORY_HEALTH_CHART_Y_PCT", "0.92") or "0.92")
_Y_HARD_MAX = float(os.getenv("INVENTORY_HEALTH_CHART_YMAX", "420") or "420")


def _chart_y_cap(ys: list[float], y_th: float) -> float:
    """可读 Y 上限：分位数 + 天数线，且不超过硬顶。"""
    floor = max(y_th * 2.0, 30.0)
    if not ys:
        return min(_Y_HARD_MAX, floor)
    positive = sorted(y for y in ys if y > 0)
    if not positive:
        return min(_Y_HARD_MAX, floor)
    idx = min(int(len(positive) * _Y_CAP_PERCENTILE), len(positive) - 1)
    pct = positive[idx]
    cap = max(floor, pct * 1.12)
    return min(cap, _Y_HARD_MAX)


def render_bubble_chart(parent, report: dict[str, Any], thresholds: dict[str, float]):
    """在 parent Tk 控件内绘制气泡图；返回 (canvas_widget, fig) 或 (label, None)。"""
    if not HAS_MPL:
        import tkinter as tk

        lbl = tk.Label(
            parent,
            text="请安装 matplotlib 以显示气泡图：pip install matplotlib",
            fg="#64748b",
        )
        lbl.pack(fill=tk.BOTH, expand=True)
        return lbl, None

    rows = [
        r for r in (report.get("rows") or [])
        if (r.get("avg_daily_demand_m3") or r.get("bubble_m3_day") or 0) > 0
        or (r.get("inventory_volume_m3") or 0) > 0
    ]
    rows.sort(key=lambda r: float(r.get("bubble_m3_day") or r.get("avg_daily_demand_m3") or 0), reverse=True)
    if len(rows) > 900:
        rows = rows[:900]
    x_th = float(thresholds.get("stockout_pct") or 50)
    y_th = float(thresholds.get("consumption_days") or 60)

    fig = Figure(figsize=(7.2, 5.4), dpi=100, facecolor="white")
    ax = fig.add_subplot(111)
    ax.set_facecolor("#fafbfc")
    ax.set_xlabel("Stockout Rate (%)", fontsize=10)
    ax.set_ylabel("Theoretical Inventory Consumption Days", fontsize=10)
    ax.set_title("Inventory Health", fontsize=11, fontweight="bold")
    ax.axvline(x_th, color="#94a3b8", linestyle="--", linewidth=1)
    ax.axhline(y_th, color="#94a3b8", linestyle="--", linewidth=1)

    xmax = x_th * 2
    ymax = y_th * 2
    xs, ys_plot, ys_true, sizes, colors, labels = [], [], [], [], [], []
    for r in rows:
        x = float(r.get("stockout_rate_pct") or 0)
        y_raw = r.get("theoretical_days")
        if y_raw is None:
            label = r.get("theoretical_days_label") or ""
            if label in ("∞", "N/A"):
                continue
            y_true = y_th * 1.5
        else:
            y_true = float(y_raw)
        b = float(r.get("bubble_m3_day") or r.get("avg_daily_demand_m3") or 0.1)
        xs.append(x)
        ys_true.append(y_true)
        sizes.append(max(24.0, min(520.0, (max(b, 0.05) ** 0.5) * 55.0)))
        quad = r.get("quadrant") or ""
        colors.append("#dc2626" if quad == "Inventory Mismatch" else "#2563eb")
        labels.append(r.get("sku") or "")

    y_cap = _chart_y_cap(ys_true, y_th)
    clipped = 0
    for y_true in ys_true:
        if y_true > y_cap:
            ys_plot.append(y_cap * 0.98)
            clipped += 1
        else:
            ys_plot.append(y_true)

    if xs:
        ax.scatter(xs, ys_plot, s=sizes, c=colors, alpha=0.55, edgecolors="#1e293b", linewidths=0.3)
        xmax = max(max(xs), xmax)
    ax.set_xlim(0, max(xmax, x_th * 2))
    ax.set_ylim(0, y_cap)
    if clipped:
        ax.text(
            0.02,
            0.98,
            f"Y 显示上限 {y_cap:.0f} 天（{clipped} 个超长库存点在顶边）",
            transform=ax.transAxes,
            fontsize=7,
            va="top",
            color="#64748b",
        )
    # 象限文字放在当前可见范围内
    ax.text(x_th * 0.5, y_cap * 0.88, QUADRANT_LABELS["tl"], ha="center", fontsize=8, color="#64748b")
    ax.text(x_th * 1.5, y_cap * 0.88, QUADRANT_LABELS["tr"], ha="center", fontsize=8, color="#dc2626", fontweight="bold")
    ax.text(x_th * 0.5, y_cap * 0.12, QUADRANT_LABELS["bl"], ha="center", fontsize=8, color="#64748b")
    ax.text(x_th * 1.5, y_cap * 0.12, QUADRANT_LABELS["br"], ha="center", fontsize=8, color="#ea580c")
    ax.grid(True, alpha=0.25)

    canvas = FigureCanvasTkAgg(fig, master=parent)
    canvas.draw()
    widget = canvas.get_tk_widget()
    widget.pack(fill="both", expand=True)
    return widget, fig
