"""Matplotlib 库存健康气泡图（嵌入 Tkinter）。"""

from __future__ import annotations

import os
import random
from collections import defaultdict
from typing import Any

from channel_prefixes import family_for_channel_link

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    font_manager = None
    FigureCanvasTkAgg = None
    Figure = None

OTHERS_ROLLUP_LABEL = "其他"
_CJK_FONT_CONFIGURED = False


def _configure_matplotlib_cjk() -> None:
    """气泡图标注中文（河北/山东等）；Windows 优先微软雅黑。"""
    global _CJK_FONT_CONFIGURED
    if not HAS_MPL or _CJK_FONT_CONFIGURED:
        return
    import os
    import sys

    candidates: list[str] = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        candidates.extend(
            [
                os.path.join(windir, "Fonts", "msyh.ttc"),
                os.path.join(windir, "Fonts", "msyhbd.ttc"),
                os.path.join(windir, "Fonts", "simhei.ttf"),
                os.path.join(windir, "Fonts", "simsun.ttc"),
            ]
        )
    candidates.extend(
        [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        ]
    )
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            font_manager.fontManager.addfont(path)
            name = font_manager.FontProperties(fname=path).get_name()
            matplotlib.rcParams["font.sans-serif"] = [name] + list(
                matplotlib.rcParams.get("font.sans-serif", [])
            )
            matplotlib.rcParams["axes.unicode_minus"] = False
            _CJK_FONT_CONFIGURED = True
            return
        except Exception:
            continue
    for name in (
        "Microsoft YaHei",
        "SimHei",
        "PingFang SC",
        "Noto Sans CJK SC",
        "WenQuanYi Micro Hei",
        "Arial Unicode MS",
    ):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            matplotlib.rcParams["font.sans-serif"] = [name] + list(
                matplotlib.rcParams.get("font.sans-serif", [])
            )
            matplotlib.rcParams["axes.unicode_minus"] = False
            _CJK_FONT_CONFIGURED = True
            return
        except Exception:
            continue
    matplotlib.rcParams["axes.unicode_minus"] = False
    _CJK_FONT_CONFIGURED = True


def sync_inventory_health_drilldown_btn(app, link_key: str | None) -> None:
    """选中气泡/表格行时：河北/山东 → 分渠道图；合并的「其他」→ 其他渠道图。"""
    btn = getattr(app, "_ih_family_btn", None)
    if btn is None:
        return
    families = getattr(app, "_ih_channel_families", None) or {}
    key = str(link_key or "").strip()
    fam = family_for_channel_link(key, families) if key else None
    app._ih_selected_family = fam
    if fam:
        app._ih_drilldown = ("family", fam)
        btn.configure(state="normal", text="分渠道图")
    elif key == OTHERS_ROLLUP_LABEL:
        app._ih_drilldown = ("other", None)
        btn.configure(state="normal", text="其他渠道图")
    else:
        app._ih_drilldown = None
        btn.configure(state="disabled", text="分渠道图")


QUADRANT_LABELS = {
    "tl": "Potential Overstock",
    "tr": "Inventory Mismatch",
    "bl": "Healthy",
    "br": "Supply Shortage",
}

# 气泡颜色 = 象限（缺货高+周转低 = 橙色告警，不是「全蓝」）
QUADRANT_COLORS = {
    "Healthy": "#22c55e",
    "Supply Shortage": "#f97316",
    "Potential Overstock": "#3b82f6",
    "Inventory Mismatch": "#dc2626",
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
        color = QUADRANT_COLORS.get(quad, "#64748b")
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
    """象限名标在绘图区外缘，避免与气泡重叠。"""
    from matplotlib.transforms import blended_transform_factory

    x_mid_l = x_th * 0.5
    x_mid_r = x_th * 1.5
    trans_bottom = blended_transform_factory(ax.transData, ax.transAxes)
    trans_top = blended_transform_factory(ax.transData, ax.transAxes)
    ax.text(
        x_mid_l,
        -0.11,
        f"{QUADRANT_LABELS['bl']}\n健康",
        transform=trans_bottom,
        ha="center",
        va="top",
        fontsize=7.5,
        color="#15803d",
        fontweight="bold",
        clip_on=False,
        zorder=12,
        bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="#bbf7d0", alpha=0.92),
    )
    ax.text(
        x_mid_r,
        -0.11,
        f"{QUADRANT_LABELS['br']}\n供应不足",
        transform=trans_bottom,
        ha="center",
        va="top",
        fontsize=7.5,
        color="#c2410c",
        fontweight="bold",
        clip_on=False,
        zorder=12,
        bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="#fed7aa", alpha=0.92),
    )
    ax.text(
        x_mid_l,
        1.05,
        f"{QUADRANT_LABELS['tl']}\n可能积压",
        transform=trans_top,
        ha="center",
        va="bottom",
        fontsize=7.5,
        color="#1d4ed8",
        clip_on=False,
        zorder=12,
        bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="#bfdbfe", alpha=0.92),
    )
    ax.text(
        x_mid_r,
        1.05,
        f"{QUADRANT_LABELS['tr']}\n库存错配",
        transform=trans_top,
        ha="center",
        va="bottom",
        fontsize=7.5,
        color="#b91c1c",
        fontweight="bold",
        clip_on=False,
        zorder=12,
        bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="#fecaca", alpha=0.92),
    )


def _chart_show_guide_panel() -> bool:
    return os.getenv("INVENTORY_HEALTH_CHART_HIDE_GUIDE", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    )


def _draw_chart_guide_banner(
    ax_guide,
    x_th: float,
    y_th: float,
    families: dict[str, list[str]] | None,
) -> None:
    """读图说明横条：放在主图上方，不占用绘图区宽度。"""
    ax_guide.set_facecolor("#f8fafc")
    ax_guide.axis("off")
    fam_bit = ""
    if families:
        fam_bit = f"  |  {'、'.join(sorted(families.keys()))} 省渠道主图合并为一个气泡"
    line1 = (
        f"读图说明：横轴缺货率%，竖虚线 {x_th:g}% 为警戒线；"
        f"纵轴理论库存消耗天（在库÷日均需求），横虚线 {y_th:g} 天；"
        f"气泡越大=日均需求体积越大。"
        f"{fam_bit}"
    )
    line2 = (
        "四象限：左下绿·健康（维持）  |  右下橙·供应不足（补货）  |  "
        "左上蓝·可能积压（控采/促销）  |  右上红·库存错配（查结构/在途）"
    )
    ax_guide.text(
        0.5,
        0.72,
        line1,
        transform=ax_guide.transAxes,
        va="top",
        ha="center",
        fontsize=7.4,
        color="#334155",
        wrap=True,
    )
    ax_guide.text(
        0.5,
        0.08,
        line2,
        transform=ax_guide.transAxes,
        va="bottom",
        ha="center",
        fontsize=7.2,
        color="#475569",
        wrap=True,
    )
    x0 = 0.04
    for label, color in (
        ("健康", QUADRANT_COLORS["Healthy"]),
        ("供应不足", QUADRANT_COLORS["Supply Shortage"]),
        ("可能积压", QUADRANT_COLORS["Potential Overstock"]),
        ("库存错配", QUADRANT_COLORS["Inventory Mismatch"]),
    ):
        ax_guide.scatter(
            [x0],
            [0.38],
            s=28,
            c=color,
            edgecolors="#334155",
            linewidths=0.3,
            transform=ax_guide.transAxes,
            clip_on=False,
        )
        x0 += 0.01


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
        sync_inventory_health_drilldown_btn(app, key)

    fig.canvas.mpl_connect("pick_event", _on_pick)

    def _on_tk_dblclick(_event=None):
        run_inventory_health_drilldown(app)

    try:
        canvas.get_tk_widget().bind("<Double-Button-1>", _on_tk_dblclick)
    except Exception:
        pass


def run_inventory_health_drilldown(app) -> None:
    """双击气泡或点「分渠道图 / 其他渠道图」。"""
    mode = getattr(app, "_ih_drilldown", None)
    report = getattr(app, "_ih_report", None) or {}
    th = getattr(app, "_ih_thresholds", {}) or {}
    root = getattr(app, "root", None)
    if not root or not mode:
        return
    if mode[0] == "family" and mode[1]:
        open_family_subchannels_chart(root, report, mode[1], th)
    elif mode[0] == "other":
        open_other_channels_chart(root, report, th)


def render_bubble_chart(
    parent,
    report: dict[str, Any],
    thresholds: dict[str, float],
    *,
    show_guide: bool | None = None,
):
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

    _configure_matplotlib_cjk()
    rows = _chart_rows(report)
    import inventory_health as ih

    x_th = float(thresholds.get("stockout_pct") or ih.DEFAULT_STOCKOUT_X)
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

    if show_guide is None:
        show_guide = _chart_show_guide_panel() and bool(report.get("channel_families") or report.get("region"))
    fams = report.get("channel_families") or {}
    if show_guide:
        fig = Figure(figsize=(8.6, 7.0), dpi=100, facecolor="white")
        gs = fig.add_gridspec(2, 1, height_ratios=[0.22, 1], hspace=0.28)
        ax_guide = fig.add_subplot(gs[0, 0])
        ax = fig.add_subplot(gs[1, 0])
    else:
        fig = Figure(figsize=(8.4, 6.2), dpi=100, facecolor="white")
        ax = fig.add_subplot(111)
        ax_guide = None
    ax.set_facecolor("#fafbfc")
    ax.set_xlabel("Stockout Rate (%)", fontsize=10)
    ax.set_ylabel("Theoretical Inventory Consumption Days", fontsize=10)
    title = "Inventory Health"
    if fams:
        bits = ", ".join(sorted(fams.keys()))
        title = f"Inventory Health ({bits} merged; other 3-digit channels separate)"
    title_pad = 28 if show_guide else 8
    ax.set_title(title, fontsize=10, fontweight="bold", pad=title_pad)
    ax.axvline(x_th, color="#94a3b8", linestyle="--", linewidth=1)
    ax.axhline(y_th, color="#94a3b8", linestyle="--", linewidth=1)

    sc_norm = _scatter_points(ax, normal)
    sc_out = None
    if outliers:
        ax.axhline(_MAIN_Y_MAX - 6, color="#fdba74", linestyle=":", linewidth=1)
        sc_out = _scatter_points(ax, outliers, marker="^")
        ax.text(
            0.02,
            0.98,
            f"{len(outliers)} 个 >{_MAIN_Y_MAX:.0f}天 叠顶栏 | 点「超长库存图」看真实天数",
            transform=ax.transAxes,
            fontsize=7,
            va="top",
            color="#9a3412",
        )
    all_labeled = normal + outliers
    _label_channels(ax, all_labeled)
    meta["pick_map"] = {}
    if sc_norm is not None:
        meta["pick_map"][sc_norm] = normal
    if sc_out is not None:
        meta["pick_map"][sc_out] = outliers
    meta["points"] = all_labeled

    xmax = x_th * 2
    if normal:
        xmax = max(max(p["x_plot"] for p in normal), xmax)
    if outliers:
        xmax = max(max(p["x_plot"] for p in outliers), xmax)
    y_cap = _MAIN_Y_MAX
    ax.set_xlim(0, max(xmax, x_th * 2))
    ax.set_ylim(0, y_cap)
    _draw_quadrant_labels(ax, x_th, y_cap)
    if ax_guide is not None:
        _draw_chart_guide_banner(ax_guide, x_th, y_th, fams if fams else None)
    else:
        for quad, color in (
            ("Supply Shortage", QUADRANT_COLORS["Supply Shortage"]),
            ("Inventory Mismatch", QUADRANT_COLORS["Inventory Mismatch"]),
            ("Potential Overstock", QUADRANT_COLORS["Potential Overstock"]),
            ("Healthy", QUADRANT_COLORS["Healthy"]),
        ):
            ax.scatter([], [], c=color, s=36, label=quad, edgecolors="#334155", linewidths=0.3)
        ax.legend(loc="lower right", fontsize=7, framealpha=0.9, title="Quadrant")
    ax.grid(True, alpha=0.25)
    ax.margins(x=0.02, y=0.02)
    fig.subplots_adjust(left=0.09, right=0.97, bottom=0.14, top=0.94 if not show_guide else 0.88)

    canvas = FigureCanvasTkAgg(fig, master=parent)
    canvas.draw()
    widget = canvas.get_tk_widget()
    widget.pack(fill="both", expand=True)
    return widget, fig, meta, canvas


def _rollup_channel_dicts(items: list[dict], label: str, th: dict[str, float]) -> dict:
    import inventory_health as ih

    inv_vol = sum(float(x.get("inventory_volume_m3") or 0) for x in items)
    demand = sum(float(x.get("avg_daily_demand_m3") or 0) for x in items)
    inv_u = sum(float(x.get("inventory_units") or 0) for x in items)
    transit = sum(float(x.get("transit_volume_m3") or 0) for x in items)
    avg_daily_u = sum(float(x.get("avg_daily_units") or 0) for x in items)
    so_weighted = []
    for x in items:
        w = float(x.get("avg_daily_demand_m3") or x.get("avg_daily_units") or 1.0)
        so = x.get("stockout_rate_pct")
        if so is not None:
            so_weighted.append((float(so), w))
    stockout = None
    if so_weighted:
        tw = sum(w for _, w in so_weighted)
        stockout = sum(v * w for v, w in so_weighted) / tw if tw else None
    hth = ih.HealthThresholds(
        stockout_pct=float(th.get("stockout_pct") or ih.DEFAULT_STOCKOUT_X),
        consumption_days=float(th.get("consumption_days") or ih.DEFAULT_DAYS_Y),
        cover_days_proxy=float(th.get("cover_days_proxy") or ih.DEFAULT_COVER_DAYS_PROXY),
    )
    th_days, th_label = ih._theoretical_days(inv_vol or None, demand or None)
    quad = ih._quadrant(stockout, th_days, hth)
    return {
        "sku": f"Σ {label}",
        "channel": label,
        "sub_channel": label,
        "stockout_rate_pct": round(stockout, 2) if stockout is not None else None,
        "theoretical_days": round(th_days, 2) if th_days is not None else None,
        "theoretical_days_label": th_label,
        "avg_daily_demand_m3": round(demand, 4) if demand else None,
        "inventory_volume_m3": round(inv_vol, 4) if inv_vol else None,
        "transit_volume_m3": round(transit, 4),
        "bubble_m3_day": round(demand, 4),
        "quadrant": quad,
        "priority": ih._priority(stockout, demand, th_days, hth),
    }


def open_other_channels_chart(
    parent,
    report: dict[str, Any],
    thresholds: dict[str, float],
):
    """非省渠道的三位号气泡图（合并「其他」时双击下钻）。"""
    if not HAS_MPL:
        return
    import inventory_health as ih

    sub_rows = ih.rows_for_other_channels(report)
    if not sub_rows:
        return
    import tkinter as tk
    from tkinter import ttk

    win = tk.Toplevel(parent)
    win.title(f"其他渠道 — {len(sub_rows)} 个三位号")
    win.geometry("900x620")
    win.transient(parent)
    frame = tk.Frame(win, bg="white")
    frame.pack(fill=tk.BOTH, expand=True)
    render_bubble_chart(
        frame, {"rows": sub_rows, "region": report.get("region")}, thresholds, show_guide=False,
    )
    ttk.Label(
        win,
        text="各非河北/山东渠道；主图默认逐个显示，无需从此进入。",
        wraplength=860,
    ).pack(anchor="w", padx=10, pady=4)
    ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 8))


def open_family_subchannels_chart(
    parent,
    report: dict[str, Any],
    family_name: str | None,
    thresholds: dict[str, float],
):
    """省渠道（河北/山东）下各三位子渠道气泡图。"""
    if not family_name or not HAS_MPL:
        return
    families = report.get("channel_families") or {}
    subs = set(families.get(family_name) or [])
    if not subs:
        return
    detail = report.get("rows_detail") or []
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in detail:
        sub = str(row.get("sub_channel") or row.get("channel") or "").strip()
        if sub in subs:
            buckets[sub].append(row)
    if not buckets:
        return

    import tkinter as tk
    from tkinter import ttk

    sub_rows = [_rollup_channel_dicts(items, sub, thresholds) for sub, items in sorted(buckets.items())]
    mini = {"rows": sub_rows, "region": report.get("region")}

    win = tk.Toplevel(parent)
    win.title(f"{family_name} — 分渠道 ({len(sub_rows)} 个)")
    win.geometry("900x620")
    win.transient(parent)

    frame = tk.Frame(win, bg="white")
    frame.pack(fill=tk.BOTH, expand=True)
    render_bubble_chart(frame, mini, thresholds, show_guide=False)
    ttk.Label(
        win,
        text=f"子渠道：{', '.join(sorted(buckets.keys()))}",
        wraplength=860,
    ).pack(anchor="w", padx=10, pady=4)
    ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 8))


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
    import inventory_health as ih

    x_th = float(thresholds.get("stockout_pct") or ih.DEFAULT_STOCKOUT_X)
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
