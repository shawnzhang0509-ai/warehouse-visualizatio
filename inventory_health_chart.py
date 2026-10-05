"""Matplotlib 库存健康气泡图（嵌入 Tkinter）。"""

from __future__ import annotations

import os
import random
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

# 主图固定可读区：超过阈值的点叠在顶栏，点击按钮看完整纵轴副图。
_MAIN_Y_MAX = float(os.getenv("INVENTORY_HEALTH_CHART_MAIN_YMAX", "150") or "150")


def _row_y_true(r: dict, y_th: float) -> float | None:
    y_raw = r.get("theoretical_days")
    if y_raw is None:
        label = r.get("theoretical_days_label") or ""
        if label in ("∞", "N/A"):
            return None
        return y_th * 1.5
    return float(y_raw)


def _bubble_size(r: dict) -> float:
    b = float(r.get("bubble_m3_day") or r.get("avg_daily_demand_m3") or 0.1)
    return max(24.0, min(520.0, (max(b, 0.05) ** 0.5) * 55.0))


def _chart_rows(report: dict[str, Any]) -> list[dict]:
    rows = [
        r for r in (report.get("rows") or [])
        if (r.get("avg_daily_demand_m3") or r.get("bubble_m3_day") or 0) > 0
        or (r.get("inventory_volume_m3") or 0) > 0
    ]
    rows.sort(key=lambda r: float(r.get("bubble_m3_day") or r.get("avg_daily_demand_m3") or 0), reverse=True)
    if len(rows) > 900:
        rows = rows[:900]
    return rows


def _link_key_for_row(r: dict) -> str:
    ch = str(r.get("channel") or "").strip()
    if ch:
        return ch
    sku = str(r.get("sku") or "").strip()
    if sku.startswith("Σ"):
        sku = sku.replace("Σ", "").strip()
    return sku[:3] if len(sku) >= 3 else sku


def _style_scatter_arrays(points: list[dict], selected_key: str | None):
    face, edge, sizes = [], [], []
    for p in points:
        selected = selected_key and p.get("link_key") == selected_key
        face.append("#fbbf24" if selected else p["color"])
        edge.append("#b45309" if selected else ("#c2410c" if p.get("outlier") else "#1e293b"))
        sizes.append(p["size"] * 1.45 if selected else p["size"])
    return face, edge, sizes


def _build_points(rows: list[dict], y_th: float) -> tuple[list[dict], list[dict]]:
    """拆成主图点 + 超长库存天（≥主图上限）点。"""
    normal: list[dict] = []
    outliers: list[dict] = []
    band_y = _MAIN_Y_MAX - 4.0
    rng = random.Random(42)
    out_idx = 0
    for r in rows:
        y_true = _row_y_true(r, y_th)
        if y_true is None:
            continue
        x = float(r.get("stockout_rate_pct") or 0)
        quad = r.get("quadrant") or ""
        color = "#dc2626" if quad == "Inventory Mismatch" else "#2563eb"
        link = _link_key_for_row(r)
        pt = {
            "row": r,
            "x": x,
            "y_true": y_true,
            "size": _bubble_size(r),
            "color": color,
            "label": r.get("sku") or "",
            "link_key": link,
            "channel_label": link,
            "outlier": False,
        }
        if y_true > _MAIN_Y_MAX:
            pt["outlier"] = True
            pt["x_plot"] = min(98.0, max(0.0, x + rng.uniform(-2.5, 2.5)))
            pt["y_plot"] = band_y + (out_idx % 5) * 0.35
            out_idx += 1
            outliers.append(pt)
        else:
            pt["x_plot"] = x
            pt["y_plot"] = y_true
            normal.append(pt)
    return normal, outliers


def _draw_quadrant_labels(ax, x_th: float, y_cap: float):
    ax.text(x_th * 0.5, y_cap * 0.88, QUADRANT_LABELS["tl"], ha="center", fontsize=8, color="#64748b")
    ax.text(x_th * 1.5, y_cap * 0.88, QUADRANT_LABELS["tr"], ha="center", fontsize=8, color="#dc2626", fontweight="bold")
    ax.text(x_th * 0.5, y_cap * 0.12, QUADRANT_LABELS["bl"], ha="center", fontsize=8, color="#64748b")
    ax.text(x_th * 1.5, y_cap * 0.12, QUADRANT_LABELS["br"], ha="center", fontsize=8, color="#ea580c")


def _scatter_points(ax, points: list[dict], *, marker="o", selected_key: str | None = None):
    if not points:
        return None
    fc, ec, sizes = _style_scatter_arrays(points, selected_key)
    return ax.scatter(
        [p["x_plot"] for p in points],
        [p["y_plot"] for p in points],
        s=sizes,
        c=fc,
        marker=marker,
        alpha=0.62,
        edgecolors=ec,
        linewidths=0.45,
        picker=True,
        pickradius=10,
        zorder=3 if marker == "o" else 4,
    )


def _label_channels(ax, points: list[dict], max_labels: int = 160):
    for p in points[:max_labels]:
        ch = p.get("channel_label") or p.get("link_key") or ""
        if not ch:
            continue
        ax.annotate(
            str(ch),
            (p["x_plot"], p["y_plot"]),
            fontsize=7,
            fontweight="bold",
            color="#0f172a",
            ha="center",
            va="bottom",
            xytext=(0, 3),
            textcoords="offset points",
            zorder=5,
        )


def highlight_chart_link(app, link_key: str | None):
    meta = getattr(app, "_ih_chart_meta", None)
    if not meta:
        return
    meta["selected_key"] = link_key or ""
    for sc, pts in meta.get("pick_map", {}).items():
        if sc is None:
            continue
        fc, ec, sizes = _style_scatter_arrays(pts, link_key or None)
        sc.set_facecolors(fc)
        sc.set_edgecolors(ec)
        sc.set_sizes(sizes)
    meta.get("canvas") and meta["canvas"].draw_idle()


def bind_chart_interaction(app, canvas, fig, meta):
    meta["canvas"] = canvas
    meta["fig"] = fig
    meta["selected_key"] = ""
    app._ih_chart_meta = meta

    def _on_pick(event):
        pts = meta.get("pick_map", {}).get(event.artist)
        if not pts:
            return
        p = pts[int(event.ind[0])]
        key = p.get("link_key") or ""
        highlight_chart_link(app, key)
        cb = getattr(app, "_ih_select_tree_by_link", None)
        if callable(cb):
            cb(key)

    fig.canvas.mpl_connect("pick_event", _on_pick)


def render_bubble_chart(parent, report: dict[str, Any], thresholds: dict[str, float]):
    """主图 Y 轴 0~150 天；超长点叠在顶栏。返回 (widget, fig, meta)。"""
    meta: dict[str, Any] = {"outliers": [], "main_y_max": _MAIN_Y_MAX}
    if not HAS_MPL:
        import tkinter as tk

        lbl = tk.Label(
            parent,
            text="请安装 matplotlib 以显示气泡图：pip install matplotlib",
            fg="#64748b",
        )
        lbl.pack(fill=tk.BOTH, expand=True)
        return lbl, None, meta, None

    rows = _chart_rows(report)
    x_th = float(thresholds.get("stockout_pct") or 50)
    y_th = float(thresholds.get("consumption_days") or 60)
    normal, outliers = _build_points(rows, y_th)
    meta["outliers"] = [
        {
            "sku": p["label"],
            "channel": p["row"].get("channel"),
            "stockout_rate_pct": p["x"],
            "theoretical_days": p["y_true"],
            "quadrant": p["row"].get("quadrant"),
            "inventory_volume_m3": p["row"].get("inventory_volume_m3"),
            "avg_daily_demand_m3": p["row"].get("avg_daily_demand_m3"),
        }
        for p in outliers
    ]
    meta["outlier_count"] = len(outliers)

    fig = Figure(figsize=(8.4, 6.0), dpi=100, facecolor="white")
    ax = fig.add_subplot(111)
    ax.set_facecolor("#fafbfc")
    ax.set_xlabel("Stockout Rate (%)", fontsize=10)
    ax.set_ylabel("Theoretical Inventory Consumption Days", fontsize=10)
    ax.set_title("Inventory Health", fontsize=11, fontweight="bold")
    ax.axvline(x_th, color="#94a3b8", linestyle="--", linewidth=1)
    ax.axhline(y_th, color="#94a3b8", linestyle="--", linewidth=1)

    sc_norm = _scatter_points(ax, normal)
    sc_out = None
    if outliers:
        ax.axhline(_MAIN_Y_MAX - 6, color="#fdba74", linestyle=":", linewidth=1)
        sc_out = _scatter_points(ax, outliers, marker="^")
    all_labeled = normal + outliers
    _label_channels(ax, all_labeled)
    meta["pick_map"] = {}
    if sc_norm is not None:
        meta["pick_map"][sc_norm] = normal
    if sc_out is not None:
        meta["pick_map"][sc_out] = outliers
    meta["points"] = all_labeled
        ax.text(
            0.02,
            0.98,
            f"▲ {len(outliers)} 个渠道/SKU 库存天 > {_MAIN_Y_MAX:.0f}，叠在顶栏；点「超长库存图」看真实天数",
            transform=ax.transAxes,
            fontsize=7,
            va="top",
            color="#9a3412",
        )

    xmax = x_th * 2
    if normal:
        xmax = max(max(p["x_plot"] for p in normal), xmax)
    if outliers:
        xmax = max(max(p["x_plot"] for p in outliers), xmax)
    y_cap = _MAIN_Y_MAX
    ax.set_xlim(0, max(xmax, x_th * 2))
    ax.set_ylim(0, y_cap)
    _draw_quadrant_labels(ax, x_th, y_cap)
    ax.grid(True, alpha=0.25)

    canvas = FigureCanvasTkAgg(fig, master=parent)
    canvas.draw()
    widget = canvas.get_tk_widget()
    widget.pack(fill="both", expand=True)
    return widget, fig, meta, canvas


def open_long_days_chart(parent, report: dict[str, Any], thresholds: dict[str, float]):
    """弹窗：仅展示库存天 > 主图上限的点，纵轴按真实天数缩放。"""
    if not HAS_MPL:
        return
    import tkinter as tk
    from tkinter import ttk

    rows = _chart_rows(report)
    y_th = float(thresholds.get("consumption_days") or 60)
    _normal, outliers = _build_points(rows, y_th)
    if not outliers:
        return

    win = tk.Toplevel(parent)
    win.title(f"库存天 > {_MAIN_Y_MAX:.0f} — 明细气泡图")
    win.geometry("820x560")
    win.transient(parent)

    fig = Figure(figsize=(8.0, 5.6), dpi=100, facecolor="white")
    ax = fig.add_subplot(111)
    ax.set_facecolor("#fafbfc")
    x_th = float(thresholds.get("stockout_pct") or 50)
    ax.set_xlabel("Stockout Rate (%)", fontsize=10)
    ax.set_ylabel("Theoretical Inventory Consumption Days (actual)", fontsize=10)
    ax.set_title(f"Long inventory days (>{_MAIN_Y_MAX:.0f}d)", fontsize=11, fontweight="bold")
    ax.axvline(x_th, color="#94a3b8", linestyle="--", linewidth=1)
    ax.axhline(y_th, color="#94a3b8", linestyle="--", linewidth=1)

    for p in outliers:
        p["x_plot"] = p["x"]
        p["y_plot"] = p["y_true"]
    _scatter_points(ax, outliers, marker="^", edge="#c2410c")

    ymax = max(p["y_true"] for p in outliers) * 1.08
    ymax = max(ymax, y_th * 2, _MAIN_Y_MAX * 1.2)
    ax.set_xlim(0, max(100.0, max(p["x"] for p in outliers) * 1.05))
    ax.set_ylim(0, ymax)
    ax.grid(True, alpha=0.25)

    for p in outliers[:24]:
        ax.annotate(
            f"{p['label']} ({p['y_true']:.0f}d)",
            (p["x"], p["y_true"]),
            fontsize=7,
            xytext=(4, 4),
            textcoords="offset points",
        )

    canvas = FigureCanvasTkAgg(fig, master=win)
    canvas.draw()
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    cols = ("sku", "channel", "days", "stockout", "quadrant")
    tree = ttk.Treeview(win, columns=cols, show="headings", height=6)
    for c, t, w in (
        ("sku", "SKU/渠道", 120),
        ("channel", "渠道", 56),
        ("days", "库存天", 72),
        ("stockout", "缺货%", 56),
        ("quadrant", "象限", 140),
    ):
        tree.heading(c, text=t)
        tree.column(c, width=w, anchor="center")
    for p in sorted(outliers, key=lambda x: -x["y_true"]):
        tree.insert(
            "",
            "end",
            values=(
                p["label"],
                p["row"].get("channel") or "",
                f"{p['y_true']:.1f}",
                f"{p['x']:.1f}",
                p["row"].get("quadrant") or "",
            ),
        )
    tree.pack(fill=tk.X, padx=8, pady=(0, 8))
    ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 8))
