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

from runner_config import load_runner_config

ROOT_DIR = Path(__file__).resolve().parent
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
                val = float(raw) if raw not in (None, "") else 0.0
            except ValueError:
                val = 0.0
            out[name] = val
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
        return _rows_to_warehouse_map(rows, master), "database", None
    except Exception as exc:
        fb = _load_csv_fallback()
        if fb:
            return fb, "csv", str(exc)
        return {}, "none", str(exc)


def channel_breakdown(region: str | None = None) -> list[dict[str, Any]]:
    region = normalize_region(region)
    master = _load_warehouse_master()
    try:
        rows = _run_query(region, CHANNEL_VOLUME_SQL)
    except Exception:
        return []
    totals: dict[str, float] = {}
    for row in rows:
        ch = (_row_get(row, "ChannelName", "") or "").strip().upper()
        wh = (_row_get(row, "WarehouseName", "") or "").strip()
        if not ch or not wh or _is_excluded(wh, master):
            continue
        m3 = float(_row_get(row, "TotalOccupiedVolume", 0) or 0)
        totals[ch] = totals.get(ch, 0.0) + _m3_to_containers(m3)
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

    rows_out = []
    total = 0.0
    unmapped = []
    for name, profile in sorted(master.items(), key=lambda x: x[0]):
        if not profile.get("include_in_volume", True):
            continue
        if _normalize_warehouse_key(name) in EXCLUDED_WAREHOUSE_KEYS:
            continue
        vol = float(volumes.get(name, 0.0))
        cap = profile.get("capacity_containers")
        util = None
        if cap and cap > 0:
            util = round(vol / float(cap) * 100, 1)
        meta = _warehouse_meta(name, master)
        if channels and vol <= 0:
            continue
        if vol > 0 or not channels:
            total += vol
            if vol > 0 and not meta.get("coords"):
                unmapped.append(name)
            rows_out.append(
                {
                    "name": name,
                    "volume_containers": round(vol, 2),
                    "volume_m3": round(_containers_to_m3(vol), 1),
                    "capacity_containers": cap,
                    "capacity_m3": meta.get("capacity_m3"),
                    "utilization_pct": util,
                    "coords": meta.get("coords"),
                    "includeInVolume": True,
                }
            )

    rows_out.sort(key=lambda r: -(r.get("volume_containers") or 0))

    return {
        "status": "success" if volumes or source == "csv" else "empty",
        "region": region,
        "filters": {"channels": channels},
        "source": source,
        "error": err,
        "total_containers": round(total, 2),
        "total_m3": round(_containers_to_m3(total), 1),
        "data": rows_out,
        "channelTotals": channel_breakdown(region) if not channels else [],
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
