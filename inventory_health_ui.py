"""库存健康看板 UI 片段（供 panel_app 嵌入）。"""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import ttk

import panel_data as pd
import inventory_health as ih
import inventory_health_chart as ihc
from channel_prefixes import load_region_po_channel_prefixes


def _ui_after(app, delay_ms: int, callback):
    """必须在主线程调度 Tk；PanelApp 只有 root.after，没有 app.after。"""
    root = getattr(app, "root", None)
    if root is not None:
        root.after(delay_ms, callback)
    else:
        callback()


def _populate_ih_owner_combo(app):
    region = app._current_region() if hasattr(app, "_current_region") else "NZ"
    rows, _path = pd.load_channel_owner_config(region)
    owners = sorted({str(r.get("owner") or "").strip() for r in rows if str(r.get("owner") or "").strip()})
    values = ["全部负责人"] + owners
    if getattr(app, "_ih_owner_combo", None) is not None:
        app._ih_owner_combo["values"] = values
        cur = app._ih_owner_var.get()
        if cur not in values:
            app._ih_owner_var.set("全部负责人")


def _populate_ih_channel_combo(app):
    region = app._current_region() if hasattr(app, "_current_region") else "NZ"
    prefixes = load_region_po_channel_prefixes(region)
    owner = (app._ih_owner_var.get() if hasattr(app, "_ih_owner_var") else "") or ""
    if owner and owner not in ("", "全部负责人"):
        rows, _ = pd.load_channel_owner_config(region)
        prefixes = [
            p for p in prefixes
            if ih._channel_matches_owner(f"{p}-000", p, owner, rows)
        ]
    values = ["全部"] + prefixes
    if getattr(app, "_ih_channel_combo", None) is not None:
        app._ih_channel_combo["values"] = values


def _snapshot_ih_filters(app) -> dict:
    """在主线程读取 Tk 变量；后台线程调用 StringVar.get() 在 Windows 上会死锁。"""
    ch = app._ih_channel_var.get()
    if str(ch).strip() in ("全部", ""):
        ch = ""
    return {
        "channel": ch,
        "category": app._ih_category_var.get(),
        "sku": app._ih_sku_var.get(),
        "branch": app._ih_branch_var.get(),
        "group_by": app._ih_group_var.get() or "sku",
        "owner": app._ih_owner_var.get() if hasattr(app, "_ih_owner_var") else "",
        "stockout_th": app._ih_stockout_th.get(),
        "days_th": app._ih_days_th.get(),
        "cover_proxy": app._ih_cover_proxy.get(),
    }


def attach_inventory_health_tab(app, notebook, style_colors: dict):
    """在 notebook 上添加「库存健康」标签页。app 需有 region_var、log 方法（可选）。"""
    tab = ttk.Frame(notebook)
    notebook.add(tab, text="库存健康")

    app._tab_inventory_health = tab
    app._ih_report = None
    app._ih_chart_frame = None
    app._ih_chart_widget = None

    app._ih_channel_var = tk.StringVar(value="全部")
    app._ih_owner_var = tk.StringVar(value="全部负责人")
    app._ih_category_var = tk.StringVar(value="")
    app._ih_sku_var = tk.StringVar(value="")
    app._ih_branch_var = tk.StringVar(value="")
    app._ih_group_var = tk.StringVar(value="channel")
    app._ih_stockout_th = tk.StringVar(value=str(ih.DEFAULT_STOCKOUT_X))
    app._ih_days_th = tk.StringVar(value=str(ih.DEFAULT_DAYS_Y))
    app._ih_cover_proxy = tk.StringVar(value=str(ih.DEFAULT_COVER_DAYS_PROXY))

    toolbar = tk.Frame(tab, bg="white")
    toolbar.pack(fill=tk.X, padx=6, pady=6)
    ttk.Button(toolbar, text="刷新", command=lambda: app._render_inventory_health(force=True)).pack(side=tk.LEFT, padx=(0, 8))
    ttk.Label(toolbar, text="负责人").pack(side=tk.LEFT)
    app._ih_owner_combo = ttk.Combobox(
        toolbar, width=10, textvariable=app._ih_owner_var, state="readonly",
    )
    app._ih_owner_combo.pack(side=tk.LEFT, padx=4)
    _populate_ih_owner_combo(app)
    app._ih_owner_combo.bind(
        "<<ComboboxSelected>>",
        lambda _e: (_populate_ih_channel_combo(app), app._render_inventory_health(force=True)),
    )
    ttk.Label(toolbar, text="渠道(前三位)").pack(side=tk.LEFT, padx=(6, 0))
    app._ih_channel_combo = ttk.Combobox(
        toolbar, width=8, textvariable=app._ih_channel_var, state="readonly",
    )
    app._ih_channel_combo.pack(side=tk.LEFT, padx=4)
    _populate_ih_channel_combo(app)
    app._ih_channel_combo.bind(
        "<<ComboboxSelected>>",
        lambda _e: app._render_inventory_health(force=True),
    )
    ttk.Label(toolbar, text="分类").pack(side=tk.LEFT)
    ttk.Entry(toolbar, width=10, textvariable=app._ih_category_var).pack(side=tk.LEFT, padx=4)
    ttk.Label(toolbar, text="SKU").pack(side=tk.LEFT)
    ttk.Entry(toolbar, width=12, textvariable=app._ih_sku_var).pack(side=tk.LEFT, padx=4)
    ttk.Label(toolbar, text="分店").pack(side=tk.LEFT)
    ttk.Entry(toolbar, width=10, textvariable=app._ih_branch_var).pack(side=tk.LEFT, padx=4)
    ttk.Label(toolbar, text="汇总").pack(side=tk.LEFT, padx=(8, 0))
    app._ih_group_combo = ttk.Combobox(
        toolbar, width=10, state="readonly", textvariable=app._ih_group_var,
        values=("channel", "sku", "category"),
    )
    app._ih_group_combo.pack(side=tk.LEFT, padx=4)
    app._ih_group_combo.bind(
        "<<ComboboxSelected>>",
        lambda _e: app._render_inventory_health(force=True),
    )
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
    chart_wrap = tk.Frame(panes, bg="white")
    chart_tool = tk.Frame(chart_wrap, bg="white")
    chart_tool.pack(fill=tk.X, padx=4, pady=(2, 0))
    app._ih_outlier_btn = ttk.Button(
        chart_tool,
        text="超长库存图 (0)",
        state=tk.DISABLED,
        command=lambda: ihc.open_long_days_chart(
            app.root,
            getattr(app, "_ih_report", None) or {},
            getattr(app, "_ih_thresholds", {}),
        ),
    )
    app._ih_outlier_btn.pack(side=tk.LEFT)
    chart_frame = tk.Frame(chart_wrap, bg="white")
    chart_frame.pack(fill=tk.BOTH, expand=True)
    table_frame = tk.Frame(panes, bg="white")
    panes.add(chart_wrap, minsize=560)
    panes.add(table_frame, minsize=200)
    app._ih_panes = panes
    app._ih_chart_frame = chart_frame

    cols = (
        "priority", "channel", "stockout", "days", "demand_m3",
        "inv_m3", "transit", "quadrant",
    )
    app._ih_tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=16)
    headings = {
        "priority": ("P", 32), "channel": ("渠道", 52),
        "stockout": ("缺货%", 52), "days": ("库存天", 52),
        "demand_m3": ("m³/天", 58),
        "inv_m3": ("在库m³", 58), "transit": ("在途m³", 58), "quadrant": ("象限", 108),
    }
    for c, (t, w) in headings.items():
        app._ih_tree.heading(c, text=t)
        app._ih_tree.column(c, width=w, anchor="center")
    scroll = ttk.Scrollbar(table_frame, orient="vertical", command=app._ih_tree.yview)
    app._ih_tree.configure(yscrollcommand=scroll.set)
    app._ih_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    app._ih_tree.tag_configure("mismatch", background="#fee2e2")
    app._ih_tree.tag_configure("selected_row", background="#fef3c7")
    app._ih_row_link: dict[str, str] = {}
    app._ih_link_to_iid: dict[str, str] = {}

    def _ih_select_tree_by_link(link_key: str):
        iid = app._ih_link_to_iid.get(link_key or "")
        if not iid:
            return
        app._ih_tree.selection_set(iid)
        app._ih_tree.see(iid)

    app._ih_select_tree_by_link = _ih_select_tree_by_link

    def _on_ih_tree_select(_event=None):
        sel = app._ih_tree.selection()
        if not sel:
            ihc.highlight_chart_link(app, None)
            return
        link = app._ih_row_link.get(sel[0], "")
        ihc.highlight_chart_link(app, link or None)

    app._ih_tree.bind("<<TreeviewSelect>>", _on_ih_tree_select)

    def _ih_place_pane_sash():
        panes = getattr(app, "_ih_panes", None)
        if not panes:
            return
        try:
            w = panes.winfo_width()
            if w > 400:
                panes.sash_place(0, int(w * 0.72), 0)
        except tk.TclError:
            pass

    app._ih_place_pane_sash = _ih_place_pane_sash

    formula = tk.Label(
        tab,
        text="渠道 = SKU 前三位；负责人来自 Output-NZ/channel_owners.csv（与负责人报表一致）；"
        "主图标注渠道前三位；点表格行 ↔ 高亮气泡；主图 0–150 天，超长点用「超长库存图」",
        bg="white", fg="#64748b", font=("Segoe UI", 8),
        wraplength=900, justify=tk.LEFT,
    )
    formula.pack(anchor="w", padx=8, pady=(0, 6))


def render_inventory_health(app, force=False):
    region = app._current_region() if hasattr(app, "_current_region") else "NZ"
    _populate_ih_owner_combo(app)
    _populate_ih_channel_combo(app)

    now = time.time()
    if getattr(app, "_ih_busy", False):
        since = float(getattr(app, "_ih_busy_since", now) or now)
        if force or (now - since) > 90:
            app._ih_busy = False
        else:
            app._ih_status.configure(
                text=f"仍在计算中…（{int(now - since)}s）仅读本地 CSV，不查数据库；超过 90s 可再点刷新",
            )
            return

    MAX_TABLE_ROWS = 600
    snap = _snapshot_ih_filters(app)

    def work():
        try:
            t0 = time.perf_counter()
            th = ih.HealthThresholds(
                stockout_pct=float(snap.get("stockout_th") or ih.DEFAULT_STOCKOUT_X),
                consumption_days=float(snap.get("days_th") or ih.DEFAULT_DAYS_Y),
                cover_days_proxy=float(snap.get("cover_proxy") or ih.DEFAULT_COVER_DAYS_PROXY),
            )

            def bump(msg):
                _ui_after(app, 0, lambda m=msg: app._ih_status.configure(text=f"库存健康：{m}"))

            owner = snap.get("owner") or ""
            if owner in ("全部负责人",):
                owner = ""
            report = ih.build_inventory_health_report(
                region,
                channel=snap.get("channel") or "",
                category=snap.get("category") or "",
                sku_filter=snap.get("sku") or "",
                branch=snap.get("branch") or "",
                owner=owner,
                group_by=snap.get("group_by") or "sku",
                thresholds=th,
                progress=bump,
            )
            elapsed = time.perf_counter() - t0
            _ui_after(app, 0, lambda r=report, e=elapsed: _finish_report(app, r, th, e, MAX_TABLE_ROWS))
        except Exception as exc:
            err = str(exc)
            _ui_after(app, 0, lambda msg=err: _fail_report(app, msg))

    if force or not getattr(app, "_ih_report", None):
        app._ih_busy = True
        app._ih_busy_since = time.time()
        app._ih_status.configure(
            text="正在计算库存健康…（仅读 Output-NZ 下 stock / sales / po.csv，不连 SQL）",
        )
        threading.Thread(target=work, daemon=True).start()


def _fail_report(app, message: str):
    app._ih_busy = False
    app._ih_status.configure(text=f"库存健康加载失败：{message}")


def _finish_report(app, report, th, elapsed_sec, max_table_rows):
    try:
        _apply_report(app, report, th, elapsed_sec, max_table_rows, draw_chart=False)
    finally:
        app._ih_busy = False
    if os.getenv("INVENTORY_HEALTH_SKIP_CHART", "").strip().lower() not in ("1", "true", "yes"):
        _ui_after(app, 80, lambda: _apply_chart(app, report, th))


def _apply_chart(app, report, th):
    try:
        app._ih_status.configure(text=str(app._ih_status.cget("text")) + " · 绘制气泡图…")
        if app._ih_chart_widget:
            app._ih_chart_widget.destroy()
        for w in app._ih_chart_frame.winfo_children():
            w.destroy()
        app._ih_thresholds = th.__dict__
        app._ih_chart_widget, _fig, chart_meta, canvas = ihc.render_bubble_chart(
            app._ih_chart_frame, report, th.__dict__,
        )
        if canvas is not None and _fig is not None:
            ihc.bind_chart_interaction(app, canvas, _fig, chart_meta)
        n_out = int(chart_meta.get("outlier_count") or 0)
        if getattr(app, "_ih_outlier_btn", None) is not None:
            app._ih_outlier_btn.configure(text=f"超长库存图 ({n_out})")
            app._ih_outlier_btn.configure(state=tk.NORMAL if n_out else tk.DISABLED)
    except Exception as exc:
        app._ih_status.configure(
            text=str(app._ih_status.cget("text")) + f" · 气泡图跳过：{exc}",
        )


def _apply_report(app, report, th, elapsed_sec=0.0, max_table_rows=600, draw_chart=True):
    app._ih_report = report
    s = report.get("summary") or {}
    cards = app._ih_card_labels
    cards["inv_m3"].configure(text=str(s.get("total_inventory_volume_m3", "—")))
    _dm = s.get("total_avg_daily_demand_m3")
    cards["demand_m3"].configure(text=str(_dm) if _dm is not None else "—")
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
    prof = meta.get("timings_sec") or {}
    prof_txt = ""
    if prof:
        prof_txt = " · " + " ".join(f"{k}={v}s" for k, v in prof.items())
    app._ih_status.configure(
        text=f"{report.get('region')} · {s.get('sku_count', 0)} 点{timing}{trunc}{prof_txt} · "
        f"销量跨度 {meta.get('sales_span_days')} 天 · {warns or meta.get('stockout_note', '')[:80]}",
    )

    for iid in app._ih_tree.get_children():
        app._ih_tree.delete(iid)
    app._ih_row_link = {}
    app._ih_link_to_iid = {}

    def _cell(val):
        if val is None:
            return "—"
        if isinstance(val, float) and val != val:
            return "—"
        return val

    for idx, row in enumerate(display_rows):
        tag = ("mismatch",) if row.get("quadrant") == "Inventory Mismatch" else ()
        link = str(row.get("channel") or "").strip()
        if not link:
            sku = str(row.get("sku") or "").strip().replace("Σ", "").strip()
            link = sku[:3] if sku else f"row{idx}"
        iid = f"ih_{link}_{idx}"
        app._ih_row_link[iid] = link
        if link not in app._ih_link_to_iid:
            app._ih_link_to_iid[link] = iid
        app._ih_tree.insert(
            "",
            "end",
            iid=iid,
            values=(
                _cell(row.get("priority")),
                _cell(row.get("channel")) or link,
                _cell(row.get("stockout_rate_pct")),
                _cell(row.get("theoretical_days_label")),
                _cell(row.get("avg_daily_demand_m3")),
                _cell(row.get("inventory_volume_m3")),
                _cell(row.get("transit_volume_m3")),
                _cell(row.get("quadrant")),
            ),
            tags=tag,
        )

    if getattr(app, "_ih_place_pane_sash", None):
        _ui_after(app, 120, app._ih_place_pane_sash)

    if draw_chart:
        _apply_chart(app, report, th)
