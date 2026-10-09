"""仓库容积率 Tab：KPI 与 Canvas 条形图（纯 Tkinter，无浏览器）。"""

from __future__ import annotations

from typing import Any

UTIL_WARN_PCT = 85.0
C_STOCK = "#2563eb"
C_STOCK_LIGHT = "#93c5fd"
C_PO = "#60a5fa"
C_PO_LIGHT = "#bfdbfe"
C_WARN = "#ea580c"
C_TEXT = "#1e293b"
C_MUTED = "#64748b"
C_CARD_BG = "#ffffff"
C_SECTION_BG = "#f8fafc"
C_ISLAND_BAR = "#1d4ed8"
C_ISLAND_BAR_2 = "#38bdf8"
C_WH_SHARE_BG = "#dbeafe"
C_WH_SHARE_FG = "#1e40af"
C_WH_SHARE_BORDER = "#2563eb"
CHART_COL_QTY_W = 50
CHART_COL_CHAN_PCT_W = 36
CHART_COL_WH_SHARE_W = 54
WH_STOCK_NUM_W = 72


def volume_kpis_from_report(report: dict[str, Any]) -> dict[str, Any]:
    po = report.get("po") or {}
    po_total = float(po.get("total_po_containers") or 0)
    stock = 0.0
    high_util: list[tuple[str, float]] = []
    for row in report.get("data") or []:
        if row.get("row_type") != "warehouse":
            continue
        stock += float(row.get("volume_containers") or 0)
        util = row.get("utilization_pct")
        if util is not None and float(util) >= UTIL_WARN_PCT:
            high_util.append((str(row.get("name") or ""), float(util)))
    high_util.sort(key=lambda x: -x[1])
    total = stock + po_total
    stock_pct = (stock / total * 100) if total > 0 else None
    po_pct = (po_total / total * 100) if total > 0 else None
    island_totals = dict(po.get("island_totals") or {})
    north_po = float(island_totals.get("北岛") or 0)
    south_po = float(island_totals.get("南岛") or 0)
    po_isl_sum = north_po + south_po
    return {
        "total_containers": round(total, 2),
        "stock_containers": round(stock, 2),
        "po_containers": round(po_total, 2),
        "stock_pct": round(stock_pct, 1) if stock_pct is not None else None,
        "po_pct": round(po_pct, 1) if po_pct is not None else None,
        "high_util": high_util,
        "north_po": round(north_po, 2),
        "south_po": round(south_po, 2),
        "north_po_pct": round(north_po / po_isl_sum * 100, 1) if po_isl_sum > 0 else None,
        "south_po_pct": round(south_po / po_isl_sum * 100, 1) if po_isl_sum > 0 else None,
    }


def island_transit_by_island(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in report.get("data") or []:
        if row.get("row_type") == "island_transit" and row.get("island"):
            out[str(row["island"])] = row
    return out


def channel_rows_split_by_island(
    channel_rows: list[dict[str, Any]],
    islands: tuple[str, ...] = ("北岛", "南岛"),
) -> list[tuple[str, list[dict[str, Any]]]]:
    """把渠道行拆成北岛/南岛两组（用 merge 结果里的 islands 字段）。"""
    buckets: dict[str, list[dict[str, Any]]] = {isl: [] for isl in islands}
    for row in channel_rows or []:
        isl_map = row.get("islands") or {}
        ch = row.get("channel") or ""
        if not isl_map:
            continue
        for isl in islands:
            d = isl_map.get(isl) or {}
            st = float(d.get("volume_containers") or 0)
            po = float(d.get("po_containers") or 0)
            if st <= 0 and po <= 0:
                continue
            buckets[isl].append({
                "channel": ch,
                "volume_containers": round(st, 2),
                "po_containers": round(po, 2),
                "total_containers": round(float(d.get("total_containers") or st + po), 2),
            })
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for isl in islands:
        rows = sorted(buckets[isl], key=lambda r: -float(r.get("total_containers") or 0))
        if rows:
            groups.append((isl, rows))
    return groups


def warehouse_rows_grouped(report: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    current_island = None
    bucket: list[dict[str, Any]] = []
    for row in report.get("data") or []:
        rt = row.get("row_type")
        if rt == "island_header":
            if bucket and current_island:
                groups.append((current_island, bucket))
            current_island = str(row.get("island") or row.get("name") or "").replace("■", "").strip()
            bucket = []
        elif rt == "warehouse" and current_island:
            bucket.append(row)
    if bucket and current_island:
        groups.append((current_island, bucket))
    return groups


def _truncate(text: str, max_len: int) -> str:
    text = str(text or "")
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _channel_chart_columns(w: int, left: int, bar_max: int) -> tuple[int, int, int]:
    col_qty = left + bar_max + 6
    col_chan_pct = col_qty + CHART_COL_QTY_W
    col_wh_share_r = max(w - 6, col_chan_pct + CHART_COL_WH_SHARE_W)
    return col_qty, col_chan_pct, col_wh_share_r


def _draw_wh_share_pill(canvas, right_x: int, cy: int, pct: float):
    pill_w = CHART_COL_WH_SHARE_W
    x0 = right_x - pill_w
    canvas.create_rectangle(
        x0, cy - 9, right_x, cy + 9,
        fill=C_WH_SHARE_BG, outline=C_WH_SHARE_BORDER, width=1,
    )
    canvas.create_text(
        x0 + pill_w / 2, cy,
        text=f"{pct:.1f}%", anchor="center",
        fill=C_WH_SHARE_FG, font=("Segoe UI", 10, "bold"),
    )


def _draw_channel_rows_block(
    canvas,
    rows: list[dict[str, Any]],
    *,
    y: int,
    w: int,
    left: int,
    bar_max: int,
    row_h: int,
    wh_stock: float,
    max_total: float,
    on_channel_click,
) -> int:
    col_qty, col_chan_pct, col_wh_share_r = _channel_chart_columns(w, left, bar_max)
    for row in rows:
        ch = str(row.get("channel") or "")
        st = float(row.get("volume_containers") or 0)
        po = float(row.get("po_containers") or 0)
        total = float(row.get("total_containers") or st + po)
        canvas.create_text(8, y + 10, text=ch, anchor="w", fill=C_TEXT, font=("Segoe UI", 9, "bold"))
        bw = bar_max * (total / max_total) if max_total else 0
        sw = bar_max * (st / max_total) if max_total else 0
        pw = max(0, bw - sw)
        x0 = left
        if sw > 0:
            canvas.create_rectangle(x0, y + 4, x0 + sw, y + 18, fill=C_STOCK, outline="")
        if pw > 0:
            canvas.create_rectangle(x0 + sw, y + 4, x0 + sw + pw, y + 18, fill=C_PO_LIGHT, outline="")
        stock_pct = (st / total * 100) if total > 0 else 0.0
        wh_share = (st / wh_stock * 100) if wh_stock > 0 else 0.0
        if bw > 28 and stock_pct > 0:
            canvas.create_text(
                x0 + min(sw, bw) / 2, y + 11,
                text=f"{stock_pct:.0f}%", anchor="center", fill="white",
                font=("Segoe UI", 8, "bold"),
            )
        canvas.create_text(
            col_qty, y + 11, text=f"{st:.2f}", anchor="w",
            fill=C_TEXT, font=("Segoe UI", 9),
        )
        canvas.create_text(
            col_chan_pct, y + 11, text=f"{stock_pct:.0f}%", anchor="w",
            fill=C_MUTED, font=("Segoe UI", 9),
        )
        _draw_wh_share_pill(canvas, col_wh_share_r, y + 11, wh_share)
        if on_channel_click and bw > 0:
            tag = f"ch_{ch}_{y}"
            canvas.create_rectangle(
                x0, y + 2, x0 + bw, y + 20, fill="", outline="", tags=(tag, "channel_bar"),
            )
            canvas.tag_bind(tag, "<Button-1>", lambda _e, c=ch: on_channel_click(c))
            canvas.tag_bind(tag, "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
            canvas.tag_bind(tag, "<Leave>", lambda _e: canvas.configure(cursor=""))
        y += row_h + 2
    return y


def draw_channel_chart(
    canvas,
    channel_rows: list[dict[str, Any]],
    *,
    top_n: int | None = None,
    warehouse_stock_total: float | None = None,
    group_by_island: bool = False,
    on_channel_click=None,
):
    """渠道横向堆叠条；top_n=None 为全部渠道（可滚动）。"""
    canvas.delete("all")
    canvas._volume_bar_meta = []  # type: ignore[attr-defined]
    w = max(int(canvas.winfo_width() or 400), 280)
    island_groups = channel_rows_split_by_island(channel_rows) if group_by_island else []
    flat_rows = sorted(
        list(channel_rows or []),
        key=lambda r: -float(r.get("total_containers") or 0),
    )
    if top_n is not None and top_n > 0:
        flat_rows = flat_rows[:top_n]
    if not flat_rows and not island_groups:
        canvas.create_text(12, 24, text="暂无渠道数据", anchor="w", fill=C_MUTED, font=("Segoe UI", 10))
        canvas.configure(scrollregion=(0, 0, w, 48))
        return
    all_for_max = flat_rows[:]
    if island_groups:
        for _isl, chunk in island_groups:
            all_for_max.extend(chunk)
    max_total = max(float(r.get("total_containers") or 0) for r in all_for_max) or 1.0
    wh_stock = float(warehouse_stock_total or 0)
    if wh_stock <= 0:
        wh_stock = sum(float(r.get("volume_containers") or 0) for r in flat_rows) or 1.0
    left = 52
    right_cols = CHART_COL_QTY_W + CHART_COL_CHAN_PCT_W + CHART_COL_WH_SHARE_W + 20
    bar_max = max(w - left - right_cols, 72)
    col_qty, col_chan_pct, col_wh_share_r = _channel_chart_columns(w, left, bar_max)
    row_h = 30
    y = 8
    n_ch = sum(len(c) for _, c in island_groups) if island_groups else len(flat_rows)
    if island_groups:
        title = f"渠道库存（北岛/南岛分开，共 {n_ch} 条渠道行）"
    elif top_n is not None and top_n > 0:
        title = f"渠道库存 TOP{len(flat_rows)}"
    else:
        title = f"渠道库存（共 {len(flat_rows)} 个，按合计降序）"
    canvas.create_text(
        12, y, text=title, anchor="w",
        fill=C_TEXT, font=("Segoe UI", 10, "bold"),
    )
    y += 22
    legend_y = y
    canvas.create_rectangle(left, legend_y, left + 12, legend_y + 10, fill=C_STOCK, outline="")
    canvas.create_text(left + 16, legend_y + 5, text="在库", anchor="w", fill=C_MUTED, font=("Segoe UI", 8))
    canvas.create_rectangle(left + 52, legend_y, left + 64, legend_y + 10, fill=C_PO_LIGHT, outline="")
    canvas.create_text(left + 68, legend_y + 5, text="在途", anchor="w", fill=C_MUTED, font=("Segoe UI", 8))
    canvas.create_text(col_qty, legend_y + 5, text="在库柜", anchor="w", fill=C_MUTED, font=("Segoe UI", 8))
    canvas.create_text(col_chan_pct, legend_y + 5, text="渠内%", anchor="w", fill=C_MUTED, font=("Segoe UI", 8))
    canvas.create_text(
        col_wh_share_r - CHART_COL_WH_SHARE_W / 2, legend_y + 5, text="占整库%",
        anchor="center", fill=C_WH_SHARE_FG, font=("Segoe UI", 8, "bold"),
    )
    y += 18
    canvas.create_text(
        12, y,
        text=f"占整库分母 = 在库合计 {wh_stock:.2f} 柜（与顶部 KPI 一致）",
        anchor="w", fill="#94a3b8", font=("Segoe UI", 8),
    )
    y += 14
    if island_groups:
        for isl, chunk in island_groups:
            canvas.create_text(
                12, y + 10, text=f"■ {isl}", anchor="w",
                fill=C_TEXT, font=("Segoe UI", 9, "bold"),
            )
            y += row_h - 2
            y = _draw_channel_rows_block(
                canvas, chunk, y=y, w=w, left=left, bar_max=bar_max, row_h=row_h,
                wh_stock=wh_stock, max_total=max_total, on_channel_click=on_channel_click,
            )
            y += 6
    else:
        y = _draw_channel_rows_block(
            canvas, flat_rows, y=y, w=w, left=left, bar_max=bar_max, row_h=row_h,
            wh_stock=wh_stock, max_total=max_total, on_channel_click=on_channel_click,
        )
    canvas.configure(scrollregion=(0, 0, w, y + 8))


def draw_warehouse_util_bars(
    canvas,
    report: dict[str, Any],
    *,
    on_wh_select=None,
    on_island_transit_click=None,
    hide_zero_stock: bool = True,
):
    canvas.delete("all")
    canvas._volume_wh_meta = []  # type: ignore[attr-defined]
    w = max(int(canvas.winfo_width() or 480), 320)
    groups = warehouse_rows_grouped(report)
    transit_map = island_transit_by_island(report)
    if hide_zero_stock:
        trimmed: list[tuple[str, list[dict[str, Any]]]] = []
        for island, rows in groups:
            kept = [r for r in rows if float(r.get("volume_containers") or 0) > 0.01]
            if kept:
                trimmed.append((island, kept))
        groups = trimmed
    if not groups:
        canvas.create_text(12, 24, text="暂无仓库在库数据", anchor="w", fill=C_MUTED, font=("Segoe UI", 10))
        canvas.configure(scrollregion=(0, 0, w, 48))
        return
    max_stock = 0.0
    for _isl, rows in groups:
        for r in rows:
            max_stock = max(max_stock, float(r.get("volume_containers") or 0))
    max_po = max(
        (float(t.get("po_containers") or 0) for t in transit_map.values()),
        default=0.0,
    )
    # 在库条与在途 PO 条共用同一柜数刻度，避免各自拉满导致长短失真
    scale_max = max(max_stock, max_po, 1.0)
    left = 118
    util_w = 52
    util_pct_w = 44
    bar_max = max(w - left - WH_STOCK_NUM_W - util_w - util_pct_w - 12, 40)
    stock_qty_x = left + bar_max + 4
    util_x0 = left + bar_max + WH_STOCK_NUM_W + 6
    util_pct_x = util_x0 + util_w + 6
    y = 8
    canvas.create_text(
        12, y, text="仓库容积率", anchor="w", fill=C_TEXT, font=("Segoe UI", 10, "bold"),
    )
    y += 18
    canvas.create_text(
        left, y,
        text=f"条长=柜数（在库与在途同一刻度，满格≈{scale_max:.0f}柜）· 右侧小条=容积率",
        anchor="w", fill=C_MUTED, font=("Segoe UI", 8),
    )
    y += 16
    row_h = 30
    warn_x = util_x0 + int(util_w * UTIL_WARN_PCT / 100)

    for island, rows in groups:
        canvas.create_text(
            12, y + 10, text=f"■ {island}", anchor="w",
            fill=C_TEXT, font=("Segoe UI", 9, "bold"),
        )
        y += row_h - 4
        for row in rows:
            name = _truncate(row.get("name") or "", 22)
            stock = float(row.get("volume_containers") or 0)
            util = row.get("utilization_pct")
            util_f = float(util) if util is not None else None
            warn = util_f is not None and util_f >= UTIL_WARN_PCT
            bar_color = C_WARN if warn else C_STOCK
            bw = bar_max * (stock / scale_max)
            canvas.create_text(20, y + 11, text=name, anchor="w", fill=C_TEXT, font=("Segoe UI", 9))
            x0 = left
            if bw > 0.5:
                canvas.create_rectangle(x0, y + 6, x0 + bw, y + 20, fill=bar_color, outline="")
            canvas.create_text(
                stock_qty_x, y + 13, text=f"{stock:.2f} 柜", anchor="w",
                fill=C_TEXT, font=("Segoe UI", 10, "bold"),
            )
            canvas.create_rectangle(util_x0, y + 8, util_x0 + util_w, y + 18, fill="#e2e8f0", outline="")
            canvas.create_line(warn_x, y + 6, warn_x, y + 20, fill="#ef4444", width=2)
            if util_f is not None:
                uw = max(2, int(util_w * min(util_f, 100) / 100))
                ucolor = C_WARN if warn else C_STOCK_LIGHT
                canvas.create_rectangle(util_x0, y + 8, util_x0 + uw, y + 18, fill=ucolor, outline="")
                canvas.create_text(
                    util_pct_x, y + 13,
                    text=f"容积率 {util_f:.1f}%", anchor="w",
                    fill=C_WARN if warn else C_TEXT, font=("Segoe UI", 9, "bold" if warn else "normal"),
                )
            else:
                canvas.create_text(util_pct_x, y + 13, text="容积率 -", anchor="w", fill=C_MUTED, font=("Segoe UI", 9))
            if on_wh_select:
                tag = f"wh_{name}"
                canvas.create_rectangle(
                    0, y, w, y + row_h, fill="", outline="", tags=(tag, "wh_row"),
                )
                canvas.tag_bind(tag, "<Button-1>", lambda _e, r=row: on_wh_select(r))
            y += row_h
        tr = transit_map.get(island)
        if tr:
            po = float(tr.get("po_containers") or 0)
            if po > 0:
                label = f"{island}在途 PO"
                canvas.create_text(20, y + 11, text=label, anchor="w", fill=C_PO, font=("Segoe UI", 9, "bold"))
                x0 = left
                pw = bar_max * (po / scale_max)
                if pw > 0.5:
                    canvas.create_rectangle(x0, y + 6, x0 + pw, y + 20, fill=C_PO_LIGHT, outline="")
                canvas.create_text(
                    stock_qty_x, y + 13, text=f"{po:.2f} 柜", anchor="w",
                    fill=C_PO, font=("Segoe UI", 10, "bold"),
                )
                canvas.create_text(
                    util_pct_x, y + 13, text="在途 · 点击查看渠道",
                    anchor="w", fill=C_PO, font=("Segoe UI", 8),
                )
                if on_island_transit_click:
                    tag = f"transit_{island}"
                    canvas.create_rectangle(
                        0, y, w, y + row_h, fill="", outline="", tags=(tag, "transit_row"),
                    )
                    canvas.tag_bind(
                        tag, "<Button-1>",
                        lambda _e, isl=island: on_island_transit_click(isl),
                    )
                    canvas.tag_bind(tag, "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
                    canvas.tag_bind(tag, "<Leave>", lambda _e: canvas.configure(cursor=""))
                y += row_h
        y += 4
    canvas.configure(scrollregion=(0, 0, w, y + 12))


def draw_island_po_strip(canvas, kpis: dict[str, Any]):
    canvas.delete("all")
    w = max(int(canvas.winfo_width() or 600), 400)
    h = 44
    north = float(kpis.get("north_po") or 0)
    south = float(kpis.get("south_po") or 0)
    total = north + south
    canvas.create_text(
        12, 10, text="在途 PO 南北岛分布", anchor="w",
        fill=C_TEXT, font=("Segoe UI", 10, "bold"),
    )
    if total <= 0:
        canvas.create_text(12, 28, text="暂无在途 PO 数据", anchor="w", fill=C_MUTED, font=("Segoe UI", 9))
        canvas.configure(scrollregion=(0, 0, w, h))
        return
    bar_y = 26
    bar_h = 14
    x0, x1 = 12, w - 12
    bar_w = x1 - x0
    nw = bar_w * (north / total)
    canvas.create_rectangle(x0, bar_y, x0 + nw, bar_y + bar_h, fill=C_ISLAND_BAR, outline="")
    canvas.create_rectangle(x0 + nw, bar_y, x1, bar_y + bar_h, fill=C_ISLAND_BAR_2, outline="")
    npct = kpis.get("north_po_pct")
    spct = kpis.get("south_po_pct")
    canvas.create_text(
        x0 + 4, bar_y + bar_h / 2,
        text=f"北岛 {north:.2f} 柜 ({npct}%)" if npct is not None else f"北岛 {north:.2f}",
        anchor="w", fill="white", font=("Segoe UI", 8, "bold"),
    )
    canvas.create_text(
        x1 - 4, bar_y + bar_h / 2,
        text=f"南岛 {south:.2f} 柜 ({spct}%)" if spct is not None else f"南岛 {south:.2f}",
        anchor="e", fill=C_TEXT, font=("Segoe UI", 8, "bold"),
    )
    canvas.configure(scrollregion=(0, 0, w, h + 8))
