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
from sql_batch import fetch_primary_result_set
from sql_placeholders import apply_sql_placeholders, placeholder_context_for_region

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
PO_CHECKIN_KEYS = [
    "checkindate", "check_in_date", "actualarrivingdate", "actual_arriving_date",
    "arrivaldate", "arriveddate", "receiveddate",
]
PO_PORT_KEYS = ["shippedtoportid", "shipped_to_port_id", "portid", "shippedtoport"]
NZ_SOUTH_PORT_ID = "3b9ff26b-4ec0-44c6-92ac-ee9804272426"
NZ_NORTH_PORT_ID = "646a2216-87c2-4bc6-8469-fdbd90bb2d84"
PORT_ID_TO_ISLAND = {
    NZ_SOUTH_PORT_ID: "南岛",
    NZ_NORTH_PORT_ID: "北岛",
}
ISLAND_ORDER = ("北岛", "南岛")
ISLAND_TRANSIT_LABEL = {
    "北岛": "北岛总在途",
    "南岛": "南岛总在途",
}
MASTER_FILE = ROOT_DIR / "warehouse_master.csv"
CONTAINER_VOLUME_M3 = 69.0

# 无 PO.txt 时 NZ 默认直查（与 SSMS 常见写法一致，含体积列）
NZ_DEFAULT_PO_SQL = """
DECLARE @SkuFilter VARCHAR(20) = '';

SELECT
    po.Id AS PurchaseOrderId,
    po.PurchaseOrderCode,
    p.Sku,
    LEFT(p.Sku, 3) AS Channel,
    pol.QuantityOrdered,
    ISNULL(p.VolumeWithBox, 0) AS VolumeWithBox,
    pol.QuantityOrdered * ISNULL(p.VolumeWithBox, 0) AS VolumeM3,
    po.[ETD],
    po.ShippedToPortId,
    c.ActualArrivingDate AS CheckinDate,
    c.ContainerNumber,
    CASE
        WHEN c.ShippedtoportId = '3b9ff26b-4ec0-44c6-92ac-ee9804272426' THEN N'南岛'
        WHEN c.ShippedtoportId = '646a2216-87c2-4bc6-8469-fdbd90bb2d84' THEN N'北岛'
        WHEN po.ShippedToPortId = '3b9ff26b-4ec0-44c6-92ac-ee9804272426' THEN N'南岛'
        WHEN po.ShippedToPortId = '646a2216-87c2-4bc6-8469-fdbd90bb2d84' THEN N'北岛'
        ELSE N'北岛'
    END AS Region
FROM dbo.PurchaseOrders po
INNER JOIN dbo.PurchaseOrderLines pol ON pol.PurchaseOrderId = po.Id
INNER JOIN dbo.Products p ON pol.ProductId = p.Id
LEFT JOIN dbo.Containers c ON c.PurchaseOrderId = po.Id
WHERE (@SkuFilter = '' OR p.Sku LIKE @SkuFilter + '%')
  AND pol.QuantityOrdered > 0
ORDER BY po.POPlacedOnUtc DESC, p.Sku;
"""

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


def _volume_stock_warehouse_excluded(name: str, master: dict) -> bool:
    """在库汇总：排除 Presale、ERP 里名为 In Transit 的虚拟仓（在途只看 po.csv + 南北岛）。"""
    if _is_excluded(name, master):
        return True
    low = str(name or "").lower()
    if "presale" in low:
        return True
    if "in transit" in low or "intransit" in low.replace(" ", ""):
        return True
    return False


def _normalize_po_island(text: str | None) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw in ("北岛", "北", "North", "NI", "North Island", "NorthIsland"):
        return "北岛"
    if raw in ("南岛", "南", "South", "SI", "South Island", "SouthIsland"):
        return "南岛"
    if "南" in raw:
        return "南岛"
    if "北" in raw:
        return "北岛"
    return ""


def _warehouse_island(name: str) -> str | None:
    n = _normalize_warehouse_key(name)
    if any(k in n for k in ("chch", "gerald", "connelly", "treffers", "christchurch")):
        return "南岛"
    if any(k in n for k in ("carbine", "walls", "wallrd")):
        return "北岛"
    if n.startswith("walls") or "wallsroad" in n:
        return "北岛"
    return None


def _row_checkin_raw(row: dict):
    for key in ("CheckinDate", "CheckInDate", "ActualArrivingDate"):
        if key in row and row[key] is not None:
            return row[key]
    return _pick_fuzzy(row, PO_CHECKIN_KEYS)


def _looks_like_checkin_date(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    low = s.lower()
    if low in ("null", "none", "nat", "n/a", "na", "#n/a", "-", "—"):
        return False
    if low.startswith(("1900-01-01", "0001-01-01", "1753-01-01")):
        return False
    if low in ("0", "0.0", "00:00:00", "00:00:00.000"):
        return False
    # 仅时间、无日期 → 不算已入库
    if len(s) <= 12 and ":" in s and "-" not in s and "/" not in s:
        return False
    return True


def _po_strict_checkin() -> bool:
    return os.getenv("WAREHOUSE_PO_STRICT_CHECKIN", "").strip().lower() in ("1", "true", "yes")


def _po_row_has_checkin(row: dict) -> bool:
    val = _row_checkin_raw(row)
    if val is None:
        return False
    return _looks_like_checkin_date(str(val))


def _island_from_po_row(row: dict) -> str:
    isl = _normalize_po_island(_pick_fuzzy(row, PO_REGION_KEYS))
    if isl:
        return isl
    port = str(_pick_fuzzy(row, PO_PORT_KEYS) or "").strip().lower()
    port = port.replace("{", "").replace("}", "")
    for pid, name in PORT_ID_TO_ISLAND.items():
        if pid.lower() in port or port in pid.lower():
            return name
    return ""


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


def _run_query_dicts(region: str, sql: str, params=None) -> list[dict]:
    """执行整段 SQL（可含 DECLARE），返回列名→值的字典列表。"""
    if pyodbc is None:
        raise RuntimeError("未安装 pyodbc，无法查询 PO")
    conn = None
    cur = None
    try:
        conn = pyodbc.connect(_connection_string(region), timeout=120)
        cur = conn.cursor()
        cur.execute(sql, params or [])
        columns, raw_rows, has_result_set = fetch_primary_result_set(cur)
        if not has_result_set:
            return []
        columns = [str(c) for c in columns]
        out: list[dict] = []
        for raw in raw_rows:
            row = {}
            for idx, name in enumerate(columns):
                val = raw[idx]
                if val is not None and hasattr(val, "isoformat"):
                    val = val.isoformat(sep=" ", timespec="seconds")
                row[name] = val
            out.append(row)
        return out
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def _region_template_dir(region: str) -> Path:
    cfg = load_runner_config()
    section = (cfg.get("regions") or {}).get(normalize_region(region)) or {}
    rel = (section.get("template_dir") or f"Data-{normalize_region(region)}").strip()
    path = Path(rel)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def _read_po_sql_template(region: str) -> tuple[str | None, str | None]:
    template_dir = _region_template_dir(region)
    if not template_dir.is_dir():
        return None, None
    preferred = ("po.sql", "po.txt", "purchase_orders.sql", "purchase_orders.txt")
    candidates = []
    for file_path in template_dir.iterdir():
        if not file_path.is_file():
            continue
        if file_path.name.lower() in preferred:
            candidates.append(file_path)
    order = {name: i for i, name in enumerate(preferred)}
    candidates.sort(key=lambda p: order.get(p.name.lower(), 99))
    for file_path in candidates:
        if ".example." in file_path.name.lower():
            continue
        for encoding in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                text = file_path.read_text(encoding=encoding).strip()
                if text:
                    return text, file_path.name
            except Exception:
                continue
    return None, None


def _write_po_csv_cache(region: str, rows: list[dict]) -> Path | None:
    if not rows or os.getenv("WAREHOUSE_PO_NO_CACHE", "").strip() in ("1", "true", "yes"):
        return None
    out_dir = _region_output_dir(region)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "po.csv"
    columns = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in columns})
    return path


def _embedded_po_sql(region: str) -> str | None:
    if normalize_region(region) == "NZ":
        return NZ_DEFAULT_PO_SQL.strip()
    return None


def _fetch_po_rows_from_template(region: str) -> tuple[list[dict], str | None, str | None]:
    errors: list[str] = []
    sql, tpl_name = _read_po_sql_template(region)
    if sql:
        try:
            sql, _ = apply_sql_placeholders(sql, placeholder_context_for_region(region))
            rows = _run_query_dicts(region, sql)
            if rows:
                return rows, tpl_name, None
            errors.append(f"{tpl_name} 查询成功但 0 行")
        except Exception as exc:
            errors.append(f"{tpl_name} 失败：{exc}")

    fallback = _embedded_po_sql(region)
    if fallback:
        try:
            rows = _run_query_dicts(region, fallback)
            if rows:
                return rows, "内置NZ_PO查询", None
            errors.append("内置 PO 查询 0 行")
        except Exception as exc:
            errors.append(f"内置 PO 查询失败：{exc}")

    if not sql and not fallback:
        return [], None, f"未找到 Data-{normalize_region(region)}/PO.txt"
    return [], tpl_name or "内置NZ_PO查询", "；".join(errors) if errors else "PO 查询无数据"


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
        if not name or _volume_stock_warehouse_excluded(name, master):
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


def _row_line_volume_m3(row: dict, qty: float) -> float:
    direct = _pick_fuzzy(row, PO_M3_KEYS)
    if direct is not None:
        return _float_cell(direct, 0.0)
    unit = _pick_fuzzy(row, PO_UNIT_VOL_KEYS)
    if unit is not None and qty > 0:
        return _float_cell(unit, 0.0) * qty
    return 0.0


def _parse_po_rows(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """仅保留尚未 check-in 的行（无有效 CheckinDate），在途体积只按南北岛汇总。"""
    lines: list[dict] = []
    stats = {
        "raw_rows": len(rows),
        "skipped_checkin": 0,
        "skipped_no_sku": 0,
        "skipped_no_qty": 0,
        "zero_volume": 0,
        "volume_fallback_qty": 0,
    }
    for row in rows:
        if _po_strict_checkin() and _po_row_has_checkin(row):
            stats["skipped_checkin"] += 1
            continue
        sku = str(_pick_fuzzy(row, PO_SKU_KEYS) or "").strip()
        if not sku:
            stats["skipped_no_sku"] += 1
            continue
        qty = _float_cell(_pick_fuzzy(row, PO_QTY_KEYS), 0.0)
        if qty <= 0:
            stats["skipped_no_qty"] += 1
            continue
        ch = str(_pick_fuzzy(row, PO_CHANNEL_KEYS) or "").strip().upper()
        if not ch:
            ch = _channel_from_sku(sku)
        island = _island_from_po_row(row)
        m3 = _row_line_volume_m3(row, qty)
        if m3 > 0:
            containers = _m3_to_containers(m3)
        else:
            stats["zero_volume"] += 1
            stats["volume_fallback_qty"] += 1
            # 无 VolumeM3/VolumeWithBox 时：数量字段按 m³ 理解，再 ÷69 换算柜（与在库一致）
            m3 = qty
            containers = _m3_to_containers(m3)
        lines.append(
            {
                "sku": sku,
                "channel": ch,
                "island": island,
                "quantity": qty,
                "volume_m3": m3,
                "volume_containers": containers,
            }
        )
    return lines, stats


def _po_stats_message(stats: dict[str, int], path: Path | None) -> str | None:
    raw = int(stats.get("raw_rows") or 0)
    kept = raw - int(stats.get("skipped_checkin") or 0) - int(stats.get("skipped_no_sku") or 0) - int(
        stats.get("skipped_no_qty") or 0
    )
    if raw <= 0:
        return None
    src = stats.get("data_source") or "po_csv"
    label = "PO.txt 直查" if src == "po_database" else "po.csv"
    tpl = stats.get("template")
    parts = [f"{label} 共 {raw} 行" + (f"（{tpl}）" if tpl else "")]
    if stats.get("skipped_checkin"):
        parts.append(f"已 Check-in 跳过 {stats['skipped_checkin']}")
    if stats.get("skipped_no_sku"):
        parts.append(f"无 SKU 跳过 {stats['skipped_no_sku']}")
    if stats.get("skipped_no_qty"):
        parts.append(f"数量≤0 跳过 {stats['skipped_no_qty']}")
    if stats.get("volume_fallback_qty"):
        parts.append(
            f"无体积字段 {stats['volume_fallback_qty']} 行已按数量÷{int(CONTAINER_VOLUME_M3)} 换算柜"
        )
    elif stats.get("zero_volume"):
        parts.append(f"在途 {kept} 行但 VolumeM3/VolumeWithBox 为 0 有 {stats['zero_volume']} 行")
    if kept <= 0 and stats.get("skipped_checkin") == raw:
        parts.append("全部行都有 CheckinDate：若仍在海上，请改 SQL 只导出未到港行，或核对柜 ActualArrivingDate")
    if path:
        parts.append(f"文件 {path.name}")
    return "；".join(parts)


def load_po_lines_from_csv(region: str | None = None) -> tuple[list[dict], Path | None]:
    """只读 Output-{region}/po.csv，不连数据库（库存健康等批量场景用）。"""
    region = normalize_region(region)
    path, csv_rows = _read_region_csv(region, PO_FILE_STEMS)
    if not csv_rows:
        return [], path
    lines, _stats = _parse_po_rows(csv_rows)
    return lines, path


def load_po_lines(region: str | None = None) -> tuple[list[dict], str | None, Path | None, dict[str, int]]:
    region = normalize_region(region)
    empty_stats: dict[str, Any] = {
        "raw_rows": 0,
        "skipped_checkin": 0,
        "skipped_no_sku": 0,
        "skipped_no_qty": 0,
        "zero_volume": 0,
        "data_source": "none",
        "template": None,
    }
    lines: list[dict] = []
    stats = dict(empty_stats)
    path = None
    sql_err = None
    sql_rows: list[dict] = []

    if pyodbc is not None:
        sql_rows, tpl_name, sql_err = _fetch_po_rows_from_template(region)
        if sql_rows:
            lines, stats = _parse_po_rows(sql_rows)
            stats["data_source"] = "po_database"
            stats["template"] = tpl_name
            path = _write_po_csv_cache(region, sql_rows)

    if not lines:
        path, csv_rows = _read_region_csv(region, PO_FILE_STEMS)
        if csv_rows:
            lines, stats = _parse_po_rows(csv_rows)
            stats["data_source"] = "po_csv"
            stats["template"] = None

    if not lines:
        diag = _po_stats_message(stats, path) if int(stats.get("raw_rows") or 0) > 0 else None
        err_parts = []
        if path is None and not sql_rows:
            err_parts.append(
                f"未找到 Output-{region}/po.csv，且 Data-{region}/PO.txt 未返回数据"
            )
        if sql_err:
            err_parts.append(f"PO.txt 执行失败：{sql_err}")
        if diag:
            err_parts.append(diag)
        if not err_parts:
            err_parts.append(
                "无在途行：请确认 SQL 含 VolumeM3 或 VolumeWithBox，且 CheckinDate 为空"
            )
        return [], "；".join(err_parts), path, stats

    err = _po_stats_message(stats, path) if int(stats.get("zero_volume") or 0) else None
    return lines, err, path, stats


def _aggregate_po(lines: list[dict]) -> tuple[dict[str, float], dict[str, float]]:
    by_channel: dict[str, float] = {}
    by_island: dict[str, float] = {}
    for line in lines:
        vol = float(line.get("volume_containers") or 0)
        ch = line.get("channel") or ""
        if ch:
            by_channel[ch] = by_channel.get(ch, 0.0) + vol
        isl = line.get("island") or ""
        if isl:
            by_island[isl] = by_island.get(isl, 0.0) + vol
    return by_channel, by_island


def build_po_report(
    region: str | None = None,
    channel_filters: list[str] | None = None,
) -> dict[str, Any]:
    region = normalize_region(region)
    channels = [c.strip().upper() for c in (channel_filters or []) if c and str(c).strip()]
    lines, err, path, stats = load_po_lines(region)
    if channels:
        lines = [ln for ln in lines if (ln.get("channel") or "").upper() in channels]
    by_ch, by_island = _aggregate_po(lines)
    if not lines and not err:
        err = _po_stats_message(stats, path)
    total = sum(by_ch.values())
    ch_rows = [
        {"channel": k, "po_containers": round(v, 2)}
        for k, v in sorted(by_ch.items(), key=lambda x: -x[1])
    ]
    island_rows = [
        {
            "island": isl,
            "name": ISLAND_TRANSIT_LABEL.get(isl, f"{isl}总在途"),
            "po_containers": round(by_island.get(isl, 0.0), 2),
        }
        for isl in ISLAND_ORDER
    ]
    return {
        "region": region,
        "source": stats.get("data_source") or ("po_csv" if path else "none"),
        "path": str(path) if path else None,
        "error": err,
        "line_count": len(lines),
        "parse_stats": stats,
        "total_po_containers": round(total, 2),
        "total_po_m3": round(_containers_to_m3(total), 1),
        "channels": ch_rows,
        "islands": island_rows,
        "island_totals": {isl: round(by_island.get(isl, 0.0), 2) for isl in ISLAND_ORDER},
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
        if wh and not _volume_stock_warehouse_excluded(wh, master):
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
            if not ch or not wh or _volume_stock_warehouse_excluded(wh, master):
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


def _build_island_volume_rows(
    volumes: dict[str, float],
    master: dict,
    po_by_island: dict[str, float],
    channels: list[str],
) -> tuple[list[dict[str, Any]], float, list[str]]:
    by_island: dict[str, list[dict[str, Any]]] = {isl: [] for isl in ISLAND_ORDER}
    unmapped: list[str] = []
    warehouse_names = set(master.keys()) | set(volumes.keys())
    for name in warehouse_names:
        if _volume_stock_warehouse_excluded(name, master):
            continue
        island = _warehouse_island(name)
        if not island:
            continue
        vol = float(volumes.get(name, 0.0))
        if channels and vol <= 0:
            continue
        profile = master.get(name) or {}
        cap = profile.get("capacity_containers")
        util = round(vol / float(cap) * 100, 1) if cap and cap > 0 else None
        meta = _warehouse_meta(name, master)
        if vol > 0 and not meta.get("coords"):
            unmapped.append(name)
        by_island[island].append(
            {
                "row_type": "warehouse",
                "island": island,
                "name": name,
                "volume_containers": round(vol, 2),
                "po_containers": 0.0,
                "total_containers": round(vol, 2),
                "volume_m3": round(_containers_to_m3(vol), 1),
                "capacity_containers": cap,
                "capacity_m3": meta.get("capacity_m3"),
                "utilization_pct": util,
                "coords": meta.get("coords"),
            }
        )

    rows_out: list[dict[str, Any]] = []
    total = 0.0
    for island in ISLAND_ORDER:
        wh_rows = sorted(by_island.get(island, []), key=lambda r: -(r.get("volume_containers") or 0))
        po_vol = float(po_by_island.get(island, 0.0))
        if channels and not wh_rows and po_vol <= 0:
            continue
        rows_out.append({"row_type": "island_header", "island": island, "name": f"■ {island}"})
        for wh in wh_rows:
            rows_out.append(wh)
            total += float(wh.get("volume_containers") or 0)
        if po_vol > 0:
            rows_out.append(
                {
                    "row_type": "island_transit",
                    "island": island,
                    "name": ISLAND_TRANSIT_LABEL[island],
                    "volume_containers": 0.0,
                    "po_containers": round(po_vol, 2),
                    "total_containers": round(po_vol, 2),
                    "volume_m3": 0.0,
                    "capacity_containers": None,
                    "capacity_m3": None,
                    "utilization_pct": None,
                    "coords": None,
                }
            )
            total += po_vol
    return rows_out, total, unmapped


def build_volume_report(
    region: str | None = None,
    channel_filters: list[str] | None = None,
) -> dict[str, Any]:
    region = normalize_region(region)
    master = _load_warehouse_master()
    volumes, source, err = query_warehouse_volumes(region, channel_filters)
    channels = [c.strip().upper() for c in (channel_filters or []) if c and str(c).strip()]
    po_report = build_po_report(region, channels or None)
    po_by_island = dict(po_report.get("island_totals") or {})

    hint = None
    if not master:
        hint = "缺少 warehouse_master.csv（仓库容量与坐标）；可从仓库根目录补全该文件"
    rows_out, total, unmapped = _build_island_volume_rows(volumes, master, po_by_island, channels)

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
    po_err = po_report.get("error")
    if po_report.get("path"):
        hint = (hint or "") + (
            f"；在途 PO {po_report.get('total_po_containers', 0)} 柜"
            f"（有效行 {po_report.get('line_count', 0)}）"
            f" 北岛 {po_by_island.get('北岛', 0)} / 南岛 {po_by_island.get('南岛', 0)} 柜"
        )
        if po_err:
            hint += f"；{po_err}"
    elif po_err and not channels:
        hint = (hint or "") + f"；{po_err}"

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
