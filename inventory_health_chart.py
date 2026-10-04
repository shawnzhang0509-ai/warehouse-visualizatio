"""Matplotlib 库存健康气泡图（嵌入 Tkinter）。"""

from __future__ import annotations

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

    rows = [r for r in (report.get("rows") or []) if r.get("stockout_rate_pct") is not None]
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
    ax.text(x_th * 0.5, y_th * 1.35, QUADRANT_LABELS["tl"], ha="center", fontsize=8, color="#64748b")
    ax.text(x_th * 1.5, y_th * 1.35, QUADRANT_LABELS["tr"], ha="center", fontsize=8, color="#dc2626", fontweight="bold")
    ax.text(x_th * 0.5, y_th * 0.35, QUADRANT_LABELS["bl"], ha="center", fontsize=8, color="#64748b")
    ax.text(x_th * 1.5, y_th * 0.35, QUADRANT_LABELS["br"], ha="center", fontsize=8, color="#ea580c")

    xs, ys, sizes, colors, labels = [], [], [], [], []
    for r in rows:
        x = float(r.get("stockout_rate_pct") or 0)
        y_raw = r.get("theoretical_days")
        if y_raw is None:
            label = r.get("theoretical_days_label") or ""
            if label in ("∞", "N/A"):
                continue
            y = y_th * 1.5
        else:
            y = float(y_raw)
        b = float(r.get("bubble_m3_day") or r.get("avg_daily_demand_m3") or 0.1)
        xs.append(x)
        ys.append(y)
        sizes.append(max(20.0, min(800.0, b * 12.0)))
        quad = r.get("quadrant") or ""
        colors.append("#dc2626" if quad == "Inventory Mismatch" else "#2563eb")
        labels.append(r.get("sku") or "")

    if xs:
        ax.scatter(xs, ys, s=sizes, c=colors, alpha=0.55, edgecolors="#1e293b", linewidths=0.3)
        xmax = max(max(xs), xmax)
        ymax = max(max(ys), ymax)
    ax.set_xlim(0, max(xmax, x_th * 2))
    ax.set_ylim(0, max(ymax, y_th * 2))
    ax.grid(True, alpha=0.25)

    canvas = FigureCanvasTkAgg(fig, master=parent)
    canvas.draw()
    widget = canvas.get_tk_widget()
    widget.pack(fill="both", expand=True)
    return widget, fig
