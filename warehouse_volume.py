"""仓库容积率：按仓库 / SKU 前三位渠道汇总占用体积（多地区 ERP 库）。

供桌面看板 panel_app「仓库容积率」页调用（纯 Tkinter，无浏览器）。
连接串来自 region_runner_config（与 SQL 执行器一致），可用环境变量 WAREHOUSE_VOLUME_REGION 覆盖默认地区。
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Any

try:
    import pyodbc
except ImportError:
    pyodbc = None

from panel_data import _pick_fuzzy, _region_output_dir
from runner_config import load_runner_config

ROOT_DIR = Path(__file__).resolve().parent
PO_FILE_STEMS = ("po", "po_in_transit", "purchase_orders")
STOCK_VOLUME_STEMS = ("stock_volume", "warehouse_stock_volume")
PO_SKU_KEYS = [
    "sku", "productsku", "productcode", "product_code", "itemcode", "code",
]
PO_QTY_KEYS = [
    "quantity", "qty", "quantityordered", "quantity_ordered",
    "poqty", "po_qty", "orderqty", "order_qty", "openqty",
]
PO_M3_KEYS = [
    "volumem3", "volume_m3", "totaloccupiedvolume", "occupiedvolume", "volume",
]
PO_UNIT_VOL_KEYS = ["volumewithbox", "volume_with_box", "unitvolume", "unit_volume"]
PO_CHANNEL_KEYS = ["channel", "channelname", "channel_code", "skuchannel"]
PO_WAREHOUSE_KEYS = [
    "warehousename", "warehouse_name", "warehouse", "destinationwarehouse", "destwarehouse",
]
PO_REGION_KEYS = ["region", "区域", "island", "南北岛", "destinationregion"]
PO_REGION_WAREHOUSE_LABEL = {
    "南岛": "南岛在途(PO)",
    "北岛": "北岛在途(PO)",
}
MASTER_FILE = ROOT_DIR / "warehouse_master.csv"
CONTAINER_VOLUME_M3 = 69.0

BASE_VOLUME_SQL = """
SELECT
    w.Name AS WarehouseName,
    SUM(s.Quantity * ISNULL(p.VolumeWithBox, 0)) AS TotalOccupiedVolume
FROM dbo.Stocks s
LEFT JOIN dbo.Products p ON s.ProductId = p.Id
LEFT JOIN dbo.Warehouses w ON s.WarehouseId = w.Id
GROUP BY w.Name
ORDER BY TotalOccupiedVolume DESC
"""

CHANNEL_VOLUME_SQL = """
SELECT
    w.Name AS WarehouseName,
    LEFT(p.SKU, 3) AS ChannelName,
    SUM(s.Quantity * ISNULL(p.VolumeWithBox, 0)) AS TotalOccupiedVolume
FROM dbo.Stocks s
LEFT JOIN dbo.Products p ON s.ProductId = p.Id
LEFT JOIN dbo.Warehouses w ON s.WarehouseId = w.Id
WHERE p.SKU IS NOT NULL
GROUP BY w.Name, LEFT(p.SKU, 3)
"""

LIST_CHANNELS_SQL = f"""
WITH ChannelVolume AS (
    {CHANNEL_VOLUME_SQL}
)
SELECT DISTINCT ChannelName
FROM ChannelVolume
WHERE ChannelName IS NOT NULL AND LTRIM(RTRIM(ChannelName)) <> ''
ORDER BY ChannelName
"""

EXCLUDED_WAREHOUSE_NAMES = [
    "Missing/To be located",
    "LV Warehouse(all dummy stock)",
    "Onehunga warehouse(No longer available)",
    "To be repaired",
    "East Tamaki -Luo(No longer available)",
    "123 Jef",
    "East Tamaki temp warehouse",
    "Otahuhu-Large",
    "Monahan Rd Warehouse(No longer available)",
    "Hamilton Old Display (No longer available)",
    "No Inventory",
    "test1",
    "Presale-In Store Only(No longer available)",
    "[Action Request]",
    "Grabone",
    "Melbourne Stock",
    "CHCH Temp",
    "Discontinued",
    "Tauranga In Transit",
    "APEX Center",
    "China_Admin Supplies",
    "SleepLAB-Onehunga",
]

_MASTER_LOCK = Lock()


def _normalize_warehouse_key(name: str) -> str:
    return "".join(ch.lower() for ch in str(name) if ch.isalnum())


EXCLUDED_WAREHOUSE_KEYS = {_normalize_warehouse_key(n) for n in EXCLUDED_WAREHOUSE_NAMES}


def normalize_region(region: str | None) -> str:
    key = str(region or os.getenv("WAREHOUSE_VOLUME_REGION") or "NZ").strip().upper()
    if key not in ("NZ", "AU", "CA"):
        return "NZ"
    return key


def _parse_bool(value, default=True):
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return default


def _m3_to_containers(m3: float) -> float:
    return float(m3 or 0) / CONTAINER_VOLUME_M3


def _containers_to_m3(containers: float) -> float:
    return float(containers or 0) * CONTAINER_VOLUME_M3


def _parse_coords(coords_text):
    if not coords_text:
        return None
    parts = [p.strip() for p in str(coords_text).split(",")]
    if len(parts) != 2:
        return None
    lat, lng = float(parts[0]), float(parts[1])
    return [lng, lat]


def _connection_string(region: str) -> str:
    override = os.getenv("WAREHOUSE_DB_CONN_STR", "").strip()
    if override:
        return override
    from app import _odbc_conn_str_from_uri

    cfg = load_runner_config()
    section = (cfg.get("regions") or {}).get(region) or {}
    uri = section.get("connection_uri") or ""
    if not uri:
        raise RuntimeError(f"未配置 {region} 的 connection_uri（region_runner_config.json）")
    return _odbc_conn_str_from_uri(uri)


def _snapshot_path(region: str) -> Path:
    return ROOT_DIR / "data" / "warehouse_volume" / region.lower() / "daily_snapshots.json"


def _load_warehouse_master() -> dict[str, dict]:
    master: dict[str, dict] = {}
    if not MASTER_FILE.is_file():
        return master
    with MASTER_FILE.open("r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("WarehouseName") or "").strip()
            if not name:
                continue
            cap_raw = row.get("CapacityM3")
            cap_m3 = float(cap_raw) if cap_raw not in (None, "") else None
            master[name] = {
                "coords": _parse_coords(row.get("Coords")),
                "include_in_volume": _parse_bool(row.get("IncludeInVolume"), True),
                "capacity_containers": _m3_to_containers(cap_m3) if cap_m3 is not None else None,
            }
    return master


def _warehouse_meta(name: str, master: dict) -> dict:
    entry = master.get(name) or {}
    include = bool(entry.get("include_in_volume", True))
    if _normalize_warehouse_key(name) in EXCLUDED_WAREHOUSE_KEYS:
        include = False
    return {
        "name": name,
        "coords": entry.get("coords"),
        "includeInVolume": include,
        "capacity_containers": entry.get("capacity_containers"),
        "capacity_m3": _containers_to_m3(entry.get("capacity_containers") or 0)
        if entry.get("capacity_containers") is not None
        else None,
    }


def _is_excluded(name: str, master: dict) -> bool:
    meta = _warehouse_meta(name, master)
    return not meta["includeInVolume"]


def _row_get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _run_query(region: str, sql: str, params=None):
    if pyodbc is None:
        raise RuntimeError("未安装 pyodbc，无法查询仓库体积")
    conn = None
    cur = None
    try:
        conn = pyodbc.connect(_connection_string(region), timeout=30)
        cur = conn.cursor()
        cur.execute(sql, params or [])
        return cur.fetchall()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def _build_channel_aggregate_sql(channel_filters: list[str]) -> str:
    placeholders = ",".join("?" for _ in channel_filters)
    return f"""
    WITH ChannelVolume AS (
        {CHANNEL_VOLUME_SQL}
    )
    SELECT WarehouseName, SUM(TotalOccupiedVolume) AS TotalOccupiedVolume
    FROM ChannelVolume
    WHERE ChannelName IN ({placeholders})
    GROUP BY WarehouseName
    ORDER BY TotalOccupiedVolume DESC
    """


def _rows_to_warehouse_map(rows, master: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        name = (_row_get(row, "WarehouseName", "") or "").strip()
        if not name or _is_excluded(name, master):
            continue
        m3 = float(_row_get(row, "TotalOccupiedVolume", 0) or 0)
        out[name] = out.get(name, 0.0) + _m3_to_containers(m3)
    return out


def list_channels(region: str | None = None) -> list[str]:
    region = normalize_region(region)
    try:
        rows = _run_query(region, LIST_CHANNELS_SQL)
        return [str(_row_get(r, "ChannelName", "")).strip() for r in rows if _row_get(r, "ChannelName")]
    except Exception:
        return []


def _read_region_csv(region: str, stems: tuple[str, ...]) -> tuple[Path | None, list[dict]]:
    out_dir = _region_output_dir(region)
    for stem in stems:
        path = out_dir / f"{stem}.csv"
        if not path.is_file():
            continue
        rows = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        return path, rows
    return None, []


def _float_cell(val, default=0.0) -> float:
    if val is None or str(val).strip() == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _channel_from_sku(sku: str) -> str:
    sku = str(sku or "").strip().upper()
    if len(sku) >= 3 and sku[:3].isdigit():
        return sku[:3]
    return sku[:3] if len(sku) >= 3 else ""


def _po_warehouse_label(row: dict) -> str:
    wh = str(_pick_fuzzy(row, PO_WAREHOUSE_KEYS) or "").strip()
    if wh:
        return wh
    region = str(_pick_fuzzy(row, PO_REGION_KEYS) or "").strip()
    if region in PO_REGION_WAREHOUSE_LABEL:
        return PO_REGION_WAREHOUSE_LABEL[region]
    return region


def _row_line_volume_m3(row: dict, qty: float) -> float:
    direct = _pick_fuzzy(row, PO_M3_KEYS)
    if direct is not None:
        return _float_cell(direct, 0.0)
    unit = _pick_fuzzy(row, PO_UNIT_VOL_KEYS)
    if unit is not None and qty > 0:
        return _float_cell(unit, 0.0) * qty
    return 0.0


def _parse_po_rows(rows: list[dict]) -> list[dict]:
    lines = []
    for row in rows:
        sku = str(_pick_fuzzy(row, PO_SKU_KEYS) or "").strip()
        if not sku:
            continue
        qty = _float_cell(_pick_fuzzy(row, PO_QTY_KEYS), 0.0)
        if qty <= 0:
            continue
        ch = str(_pick_fuzzy(row, PO_CHANNEL_KEYS) or "").strip().upper()
        if not ch:
            ch = _channel_from_sku(sku)
        wh = _po_warehouse_label(row)
        m3 = _row_line_volume_m3(row, qty)
        lines.append(
            {
                "sku": sku,
                "channel": ch,
                "warehouse": wh,
                "quantity": qty,
                "volume_m3": m3,
                "volume_containers": _m3_to_containers(m3),
            }
        )
    return lines


def load_po_lines(region: str | None = None) -> tuple[list[dict], str | None, Path | None]:
    region = normalize_region(region)
    path, rows = _read_region_csv(region, PO_FILE_STEMS)
    if path is None:
        return [], "未找到 Output-{0}/po.csv：请在 Data-{0} 放 PO.txt 并执行 SQL 导出".format(region), None
    if not rows:
        return [], f"po.csv 为空：{path.name}", path
    return _parse_po_rows(rows), None, path


def _aggregate_po(lines: list[dict]) -> tuple[dict[str, float], dict[str, float]]:
    by_channel: dict[str, float] = {}
    by_wh: dict[str, float] = {}
    for line in lines:
        ch = line.get("channel") or ""
        if ch:
            by_channel[ch] = by_channel.get(ch, 0.0) + float(line.get("volume_containers") or 0)
        wh = line.get("warehouse") or ""
        if wh:
            by_wh[wh] = by_wh.get(wh, 0.0) + float(line.get("volume_containers") or 0)
    return by_channel, by_wh


def build_po_report(
    region: str | None = None,
    channel_filters: list[str] | None = None,
) -> dict[str, Any]:
    region = normalize_region(region)
    channels = [c.strip().upper() for c in (channel_filters or []) if c and str(c).strip()]
    lines, err, path = load_po_lines(region)
    if channels:
        lines = [ln for ln in lines if (ln.get("channel") or "").upper() in channels]
    by_ch, by_wh = _aggregate_po(lines)
    total = sum(by_ch.values())
    ch_rows = [
        {"channel": k, "po_containers": round(v, 2)}
        for k, v in sorted(by_ch.items(), key=lambda x: -x[1])
    ]
    return {
        "region": region,
        "source": "po_csv" if path else "none",
        "path": str(path) if path else None,
        "error": err,
        "line_count": len(lines),
        "total_po_containers": round(total, 2),
        "total_po_m3": round(_containers_to_m3(total), 1),
        "channels": ch_rows,
        "warehouses": [
            {"name": k, "po_containers": round(v, 2)}
            for k, v in sorted(by_wh.items(), key=lambda x: -x[1])
        ],
    }


def _stock_volume_rows_to_maps(
    rows: list[dict], master: dict
) -> tuple[dict[str, float], dict[str, float]]:
    by_wh: dict[str, float] = {}
    by_ch: dict[str, float] = {}
    for row in rows:
        wh = str(_pick_fuzzy(row, PO_WAREHOUSE_KEYS) or row.get("WarehouseName") or "").strip()
        sku = str(_pick_fuzzy(row, PO_SKU_KEYS) or "").strip()
        qty = _float_cell(_pick_fuzzy(row, PO_QTY_KEYS), 0.0)
        m3 = _row_line_volume_m3(row, qty)
        if m3 <= 0:
            continue
        containers = _m3_to_containers(m3)
        ch = str(_pick_fuzzy(row, PO_CHANNEL_KEYS) or "").strip().upper() or _channel_from_sku(sku)
        if wh and not _is_excluded(wh, master):
            by_wh[wh] = by_wh.get(wh, 0.0) + containers
        if ch:
            by_ch[ch] = by_ch.get(ch, 0.0) + containers
    return by_wh, by_ch


def _load_stock_volume_maps(region: str) -> tuple[dict[str, float], dict[str, float], Path | None]:
    master = _load_warehouse_master()
    path, rows = _read_region_csv(region, STOCK_VOLUME_STEMS)
    if path is None or not rows:
        return {}, {}, None
    return (*_stock_volume_rows_to_maps(rows, master), path)


def merge_channel_breakdown(
    stock_rows: list[dict[str, Any]],
    po_report: dict[str, Any],
) -> list[dict[str, Any]]:
    stock_map = {
        str(r.get("channel") or "").upper(): float(r.get("volume_containers") or 0)
        for r in stock_rows
    }
    po_map = {
        str(r.get("channel") or "").upper(): float(r.get("po_containers") or 0)
        for r in (po_report.get("channels") or [])
    }
    keys = sorted(set(stock_map) | set(po_map), key=lambda k: (-(stock_map.get(k, 0) + po_map.get(k, 0)), k))
    out = []
    for ch in keys:
        if not ch:
            continue
        stock_v = round(stock_map.get(ch, 0.0), 2)
        po_v = round(po_map.get(ch, 0.0), 2)
        out.append(
            {
                "channel": ch,
                "volume_containers": stock_v,
                "po_containers": po_v,
                "total_containers": round(stock_v + po_v, 2),
            }
        )
    return out


def _load_csv_fallback() -> dict[str, float]:
    path = ROOT_DIR / "data.csv"
    if not path.is_file():
        return {}
    master = _load_warehouse_master()
    out: dict[str, float] = {}
    with path.open("r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("WarehouseName") or "").strip()
            if not name or _is_excluded(name, master):
                continue
            raw = row.get("TotalOccupiedVolume")
            try:
                m3 = float(raw) if raw not in (None, "") else 0.0
            except ValueError:
                m3 = 0.0
            out[name] = _m3_to_containers(m3)
    return out


def query_warehouse_volumes(
    region: str | None = None,
    channel_filters: list[str] | None = None,
) -> tuple[dict[str, float], str, str | None]:
    """返回 (仓库名→占用柜数, 数据来源 database|csv, 错误信息)。"""
    region = normalize_region(region)
    master = _load_warehouse_master()
    channels = [c.strip().upper() for c in (channel_filters or []) if c and str(c).strip()]

    try:
        if channels:
            sql = _build_channel_aggregate_sql(channels)
            rows = _run_query(region, sql, channels)
        else:
            rows = _run_query(region, BASE_VOLUME_SQL)
        volumes = _rows_to_warehouse_map(rows, master)
        if not volumes or sum(volumes.values()) <= 0:
            sv_wh, _, sv_path = _load_stock_volume_maps(region)
            if sv_wh:
                return sv_wh, "stock_volume_csv", None
        return volumes, "database", None
    except Exception as exc:
        sv_wh, _, sv_path = _load_stock_volume_maps(region)
        if sv_wh:
            return sv_wh, "stock_volume_csv", str(exc)
        fb = _load_csv_fallback()
        if fb:
            return fb, "csv", str(exc)
        return {}, "none", str(exc)


def channel_breakdown(region: str | None = None) -> list[dict[str, Any]]:
    region = normalize_region(region)
    master = _load_warehouse_master()
    totals: dict[str, float] = {}
    try:
        rows = _run_query(region, CHANNEL_VOLUME_SQL)
        for row in rows:
            ch = (_row_get(row, "ChannelName", "") or "").strip().upper()
            wh = (_row_get(row, "WarehouseName", "") or "").strip()
            if not ch or not wh or _is_excluded(wh, master):
                continue
            m3 = float(_row_get(row, "TotalOccupiedVolume", 0) or 0)
            totals[ch] = totals.get(ch, 0.0) + _m3_to_containers(m3)
    except Exception:
        totals = {}
    if not totals:
        _, by_ch, _path = _load_stock_volume_maps(region)
        totals = dict(by_ch)
    ranked = sorted(totals.items(), key=lambda x: -x[1])
    return [{"channel": k, "volume_containers": round(v, 2)} for k, v in ranked]


def build_volume_report(
    region: str | None = None,
    channel_filters: list[str] | None = None,
) -> dict[str, Any]:
    region = normalize_region(region)
    master = _load_warehouse_master()
    volumes, source, err = query_warehouse_volumes(region, channel_filters)
    channels = [c.strip().upper() for c in (channel_filters or []) if c and str(c).strip()]
    po_report = build_po_report(region, channels or None)
    po_by_wh = {w["name"]: w["po_containers"] for w in (po_report.get("warehouses") or [])}

    warehouse_names = set(master.keys()) | set(volumes.keys()) | set(po_by_wh.keys())
    rows_out = []
    total = 0.0
    unmapped = []
    hint = None
    if not master:
        hint = "缺少 warehouse_master.csv（仓库容量与坐标）；可从仓库根目录补全该文件"
    for name in sorted(warehouse_names, key=lambda x: x.lower()):
        profile = master.get(name) or {}
        if profile and not profile.get("include_in_volume", True):
            continue
        if _normalize_warehouse_key(name) in EXCLUDED_WAREHOUSE_KEYS:
            continue
        vol = float(volumes.get(name, 0.0))
        po_vol = float(po_by_wh.get(name, 0.0))
        cap = profile.get("capacity_containers")
        util = None
        if cap and cap > 0:
            util = round((vol + po_vol) / float(cap) * 100, 1)
        meta = _warehouse_meta(name, master)
        if channels and vol <= 0 and po_vol <= 0:
            continue
        if vol > 0 or po_vol > 0 or not channels:
            total += vol + po_vol
            if vol > 0 and not meta.get("coords"):
                unmapped.append(name)
            rows_out.append(
                {
                    "name": name,
                    "volume_containers": round(vol, 2),
                    "po_containers": round(po_vol, 2),
                    "total_containers": round(vol + po_vol, 2),
                    "volume_m3": round(_containers_to_m3(vol), 1),
                    "capacity_containers": cap,
                    "capacity_m3": meta.get("capacity_m3"),
                    "utilization_pct": util,
                    "coords": meta.get("coords"),
                    "includeInVolume": True,
                }
            )

    rows_out.sort(key=lambda r: -(r.get("total_containers") or r.get("volume_containers") or 0))

    if source == "database" and not channels and (total <= 0 or not volumes):
        hint = (
            hint
            or "占用为 0 时请核对 iERP 产品字段 VolumeWithBox（体积）是否维护；"
            "本页不读 stock.csv，需能连 SQL Server（Windows 需 ODBC Driver 18）"
        )
    elif source == "csv" and err:
        hint = hint or f"数据库不可用，已用 data.csv 快照：{err}"
    elif source == "stock_volume_csv":
        hint = hint or "在库体积来自 Output 下 stock_volume.csv（由 Data 目录 stock_volume.txt 导出）"
    if po_report.get("path"):
        hint = (hint or "") + (
            f"；在途 PO {po_report.get('total_po_containers', 0)} 柜"
            f"（{po_report.get('line_count', 0)} 行 po.csv）"
        )
    elif po_report.get("error") and not channels:
        hint = (hint or "") + f"；{po_report.get('error')}"

    return {
        "status": "success" if volumes or source == "csv" else "empty",
        "region": region,
        "filters": {"channels": channels},
        "source": source,
        "error": err,
        "hint": hint,
        "total_containers": round(total, 2),
        "total_m3": round(_containers_to_m3(total), 1),
        "data": rows_out,
        "channelTotals": channel_breakdown(region) if not channels else [],
        "po": po_report,
        "unmappedWarehouses": unmapped,
        "container_m3": CONTAINER_VOLUME_M3,
    }


def map_markers_payload(report: dict) -> list[dict]:
    markers = []
    for row in report.get("data") or []:
        coords = row.get("coords")
        if not coords:
            continue
        markers.append(
            {
                "name": row["name"],
                "value": [*coords, row.get("volume_containers") or 0],
                "utilization_pct": row.get("utilization_pct"),
            }
        )
    return markers
