import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request, send_file
try:
    import pyodbc
except ImportError:
    pyodbc = None

app = Flask(__name__)

# 1) 数据库连接信息（优先读取环境变量，便于部署）
DEFAULT_CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=if-akl-live.database.windows.net;"
    "DATABASE=nz_ierp_live;"
    "UID=nzlivepooluser;"
    "PWD=iFur3RP@5sc^l^t3!;"
)
CONN_STR = os.getenv("WAREHOUSE_DB_CONN_STR", DEFAULT_CONN_STR)

# 2) 仓库体积（全量）SQL：按仓库汇总
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

# 3) 渠道体积 SQL（来自 SKU 前三位渠道）
# 输出字段固定为：
#   WarehouseName | ChannelName | TotalOccupiedVolume
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

# 4) 每日渠道趋势 SQL（可选）
# 若为 None，则自动尝试使用 dbo.Stocks 的日期字段构建 SQL。
# 若你提供自定义 SQL，需输出字段：
#   VolumeDate(date/datetime), ChannelName, TotalOccupiedVolume
DAILY_CHANNEL_TREND_SQL = None
SNAPSHOT_FILE = Path(__file__).with_name("data").joinpath("daily_snapshots.json")
CONTAINER_VOLUME_M3 = 69.0

# 这些仓库不参与任何体积计算（总量/渠道/趋势）
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


def _normalize_warehouse_key(name):
    return "".join(ch.lower() for ch in str(name) if ch.isalnum())


EXCLUDED_WAREHOUSE_KEYS = {
    _normalize_warehouse_key(name) for name in EXCLUDED_WAREHOUSE_NAMES
}


def _is_excluded_warehouse(name):
    return _normalize_warehouse_key(name) in EXCLUDED_WAREHOUSE_KEYS


def _parse_coords(coords_text):
    """CSV 内坐标是 '纬度, 经度'，前端 ECharts 需要 [经度, 纬度]。"""
    if not coords_text:
        return None
    parts = [p.strip() for p in str(coords_text).split(",")]
    if len(parts) != 2:
        return None
    lat = float(parts[0])
    lng = float(parts[1])
    return [lng, lat]


def _m3_to_container(value):
    return float(value or 0) / CONTAINER_VOLUME_M3


def _convert_row_to_containers(row):
    if isinstance(row, dict):
        mapped = dict(row)
        mapped["TotalOccupiedVolume"] = _m3_to_container(
            mapped.get("TotalOccupiedVolume", 0)
        )
        return mapped

    class _Obj:
        pass

    out = _Obj()
    for key in dir(row):
        if key.startswith("_"):
            continue
        try:
            setattr(out, key, getattr(row, key))
        except Exception:
            continue
    out.TotalOccupiedVolume = _m3_to_container(
        _row_get(row, "TotalOccupiedVolume", 0)
    )
    return out


def _convert_rows_to_containers(rows):
    return [_convert_row_to_containers(row) for row in rows]


def _load_warehouse_meta():
    meta = {}
    csv_path = Path(__file__).with_name("data.csv")
    if not csv_path.exists():
        return meta

    with csv_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("WarehouseName") or "").strip()
            if not name:
                continue
            capacity_raw = row.get("Capacity")
            capacity_m3 = (
                float(capacity_raw) if capacity_raw not in (None, "") else None
            )
            meta[name] = {
                "coords": _parse_coords(row.get("zuobiao")),
                "capacity": (
                    _m3_to_container(capacity_m3)
                    if capacity_m3 is not None
                    else None
                ),
            }
    return meta


WAREHOUSE_META = _load_warehouse_meta()


def _load_base_rows_from_csv():
    rows = []
    csv_path = Path(__file__).with_name("data.csv")
    if not csv_path.exists():
        return rows
    with csv_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("WarehouseName") or "").strip()
            if not name:
                continue
            if _is_excluded_warehouse(name):
                continue
            volume_raw = row.get("TotalOccupiedVolume")
            volume_m3 = float(volume_raw) if volume_raw not in (None, "") else 0.0
            volume = _m3_to_container(volume_m3)
            rows.append(
                {
                    "WarehouseName": name,
                    "TotalOccupiedVolume": volume,
                }
            )
    rows.sort(key=lambda item: item["TotalOccupiedVolume"], reverse=True)
    return rows


def _load_snapshot_store():
    if not SNAPSHOT_FILE.exists():
        return {"last_updated": None, "days": []}
    try:
        with SNAPSHOT_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"last_updated": None, "days": []}
            if "days" not in data or not isinstance(data["days"], list):
                data["days"] = []
            if "last_updated" not in data:
                data["last_updated"] = None
            return data
    except Exception:
        return {"last_updated": None, "days": []}


def _save_snapshot_store(store):
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SNAPSHOT_FILE.open("w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def _snapshot_rows_to_map(rows):
    mapped = {}
    for row in rows:
        name = (_row_get(row, "WarehouseName", "") or "").strip()
        if not name:
            continue
        if _is_excluded_warehouse(name):
            continue
        mapped[name] = _m3_to_container(_row_get(row, "TotalOccupiedVolume", 0) or 0)
    return mapped


def _snapshot_channel_rows_to_map(rows):
    mapped = {}
    for row in rows:
        channel = (_row_get(row, "ChannelName", "") or "").strip()
        warehouse = (_row_get(row, "WarehouseName", "") or "").strip()
        volume = _m3_to_container(_row_get(row, "TotalOccupiedVolume", 0) or 0)
        if not channel or not warehouse:
            continue
        if _is_excluded_warehouse(warehouse):
            continue
        channel_key = channel.upper()
        if channel_key not in mapped:
            mapped[channel_key] = {}
        mapped[channel_key][warehouse] = volume
    return mapped


def _snapshot_total_by_channel(channel_filters, channel_map):
    if not channel_filters:
        total = 0.0
        for warehouses in channel_map.values():
            total += sum(float(v) for v in warehouses.values())
        return total
    total = 0.0
    matched = False
    for channel in channel_filters:
        warehouses = channel_map.get(channel.upper(), {})
        if warehouses:
            matched = True
            total += sum(float(v) for v in warehouses.values())
    # 静态快照只有 ALL 聚合时，渠道筛选回退到最新全量快照，避免报错或空图。
    if not matched and "ALL" in channel_map:
        return sum(float(v) for v in channel_map["ALL"].values())
    return total


def _snapshot_map_rows(channel_filters, base_map, channel_map):
    if not channel_filters:
        if base_map:
            return [
                {"WarehouseName": name, "TotalOccupiedVolume": volume}
                for name, volume in base_map.items()
            ]
        if "ALL" in channel_map:
            return [
                {"WarehouseName": name, "TotalOccupiedVolume": volume}
                for name, volume in channel_map["ALL"].items()
            ]
        return []
    aggregate = {}
    for channel in channel_filters:
        warehouses = channel_map.get(channel.upper(), {})
        for name, volume in warehouses.items():
            aggregate[name] = aggregate.get(name, 0.0) + float(volume)
    if aggregate:
        return [
            {"WarehouseName": name, "TotalOccupiedVolume": volume}
            for name, volume in aggregate.items()
        ]
    if "ALL" in channel_map:
        return [
            {"WarehouseName": name, "TotalOccupiedVolume": volume}
            for name, volume in channel_map["ALL"].items()
        ]
    return []


def _build_snapshot_trend(days, channel_filters, store):
    items = store.get("days", [])
    if not items:
        return []
    # 同一天如果有多次刷新的记录，仅保留最新一条，避免折线重复累加。
    latest_by_date = {}
    for item in items:
        date_text = item.get("date")
        if date_text:
            latest_by_date[date_text] = item
    selected = [latest_by_date[d] for d in sorted(latest_by_date.keys())][-days:]
    points = []
    for item in selected:
        date_text = item.get("date")
        if not date_text:
            continue
        channel_map = item.get("channels", {}) if isinstance(item.get("channels"), dict) else {}
        volume = _snapshot_total_by_channel(channel_filters, channel_map)
        points.append({"date": date_text, "channel": "ALL", "volume": float(volume)})
    return points


def _refresh_daily_snapshot(force=False):
    """按天刷新快照：同一天只采一次（建议由外部调度在 00:00 调用）。"""
    today = date.today().isoformat()
    store = _load_snapshot_store()
    if (
        not force
        and store.get("days")
        and store["days"][-1].get("date") == today
    ):
        return store, False

    # 优先数据库，失败则使用 CSV 快照
    source = "database"
    try:
        base_rows = _run_query(BASE_VOLUME_SQL)
        base_rows = _convert_rows_to_containers(base_rows)
    except Exception:
        source = "csv"
        base_rows = _load_base_rows_from_csv()

    base_map = _snapshot_rows_to_map(base_rows)
    channel_map = {}
    if source == "database":
        try:
            channel_rows = _run_query(CHANNEL_VOLUME_SQL)
            channel_rows = _convert_rows_to_containers(channel_rows)
            channel_map = _snapshot_channel_rows_to_map(channel_rows)
        except Exception:
            channel_map = {}

    # 若没有渠道明细，至少保留 ALL 聚合
    if not channel_map:
        channel_map = {"ALL": base_map}

    # force 刷新同一天时，先覆盖当天旧快照，避免趋势重复日期累计。
    store["days"] = [item for item in store.get("days", []) if item.get("date") != today]
    store["days"].append(
        {
            "date": today,
            "source": source,
            "base": base_map,
            "channels": channel_map,
        }
    )
    # 仅保留最近 366 天
    store["days"] = store["days"][-366:]
    store["last_updated"] = today
    _save_snapshot_store(store)
    return store, True


def _csv_total_volume():
    return sum(item["TotalOccupiedVolume"] for item in _load_base_rows_from_csv())


def _sum_rows_total(rows):
    total = 0.0
    for row in rows:
        warehouse_name = (_row_get(row, "WarehouseName", "") or "").strip()
        if warehouse_name and _is_excluded_warehouse(warehouse_name):
            continue
        total += float(_row_get(row, "TotalOccupiedVolume", 0) or 0)
    return total


def _normalize_name(name):
    return _normalize_warehouse_key(name)


def _get_warehouse_meta(warehouse_name):
    if warehouse_name in WAREHOUSE_META:
        return WAREHOUSE_META[warehouse_name]

    target = _normalize_name(warehouse_name)
    if not target:
        return {}

    # 名称轻微不一致时，做一次归一化模糊匹配（仓库数量少，线性扫描足够）
    for name, meta in WAREHOUSE_META.items():
        normalized = _normalize_name(name)
        if normalized == target or target in normalized or normalized in target:
            return meta
    return {}


def _run_query(sql, params=None):
    if pyodbc is None:
        raise RuntimeError("数据库驱动不可用（pyodbc / libodbc 未安装）")
    conn = None
    cursor = None
    try:
        conn = pyodbc.connect(CONN_STR)
        cursor = conn.cursor()
        cursor.execute(sql, params or [])
        return cursor.fetchall()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _build_channel_sql(channel_filters):
    if not CHANNEL_VOLUME_SQL:
        raise ValueError("尚未配置 CHANNEL_VOLUME_SQL，请先粘贴渠道体积查询 SQL。")
    placeholders = ",".join("?" for _ in channel_filters)
    return f"""
    WITH ChannelVolume AS (
        {CHANNEL_VOLUME_SQL}
    )
    SELECT
        WarehouseName,
        SUM(TotalOccupiedVolume) AS TotalOccupiedVolume
    FROM ChannelVolume
    WHERE ChannelName IN ({placeholders})
    GROUP BY WarehouseName
    ORDER BY TotalOccupiedVolume DESC
    """


def _filter_rows_by_excluded_warehouses(rows):
    filtered = []
    for row in rows:
        name = (_row_get(row, "WarehouseName", "") or "").strip()
        if not name or _is_excluded_warehouse(name):
            continue
        filtered.append(row)
    return filtered


def _parse_days():
    raw = request.args.get("days", "30").strip()
    try:
        days = int(raw)
    except ValueError:
        days = 30
    if days < 1:
        return 30
    return min(days, 365)


def _parse_channel_filters():
    # 支持 /get-data?channel=A&channel=B 和 /get-data?channels=A,B
    channels = request.args.getlist("channel")
    if not channels:
        channels_csv = request.args.get("channels", "")
        channels = channels_csv.split(",") if channels_csv else []
    return [ch.strip() for ch in channels if ch and ch.strip()]


def _row_get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _build_flat_trend(days, total):
    points = []
    end = date.today()
    for idx in range(days - 1, -1, -1):
        d = end - timedelta(days=idx)
        points.append(
            {
                "date": d.isoformat(),
                "channel": "ALL",
                "volume": float(total),
            }
        )
    return points


def _latest_snapshot_entry():
    store = _load_snapshot_store()
    days = store.get("days", [])
    if not days:
        return store, None
    return store, days[-1]


def _rows_from_snapshot_store(channel_filters):
    store, latest = _latest_snapshot_entry()
    if not latest:
        return None, None
    base_map = latest.get("base", {}) if isinstance(latest.get("base"), dict) else {}
    channel_map = (
        latest.get("channels", {})
        if isinstance(latest.get("channels"), dict)
        else {}
    )
    rows = _snapshot_map_rows(channel_filters, base_map, channel_map)
    rows.sort(
        key=lambda item: float(item.get("TotalOccupiedVolume", 0) or 0),
        reverse=True,
    )
    return rows, latest.get("date")


def _fallback_total_for_filters(channel_filters):
    rows, _snapshot_date = _rows_from_snapshot_store(channel_filters)
    if rows is not None:
        total = 0.0
        for row in rows:
            total += float(row.get("TotalOccupiedVolume", 0) or 0)
        return total, "snapshot"
    if channel_filters:
        try:
            sql_query = _build_channel_sql(channel_filters)
            rows = _run_query(sql_query, channel_filters)
            rows = _convert_rows_to_containers(rows)
            return _sum_rows_total(rows), "database_channel_snapshot"
        except Exception:
            return _csv_total_volume(), "csv_snapshot_no_channel_breakdown"
    try:
        rows = _run_query(BASE_VOLUME_SQL)
        rows = _convert_rows_to_containers(rows)
        return _sum_rows_total(rows), "database_snapshot"
    except Exception:
        return _csv_total_volume(), "csv_snapshot"


try:
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
except FileNotFoundError:
    html_content = "<h1>Error: index.html not found</h1>"


@app.route("/")
def index():
    return render_template_string(html_content)


@app.route("/nz.json")
def nz_geojson():
    nz_path = Path(__file__).with_name("nz.json")
    if not nz_path.exists():
        return jsonify({"status": "error", "message": "nz.json not found"}), 404
    return send_file(nz_path, mimetype="application/json")


@app.route("/get-data")
def get_data():
    try:
        channel_filters = _parse_channel_filters()
        fallback_used = False
        fallback_reason = None
        snapshot_date = None

        # 地图默认走“每日快照”数据，满足“每天 00:00 存一次，其余时间看静态值”。
        rows, snapshot_date = _rows_from_snapshot_store(channel_filters)
        if rows is None:
            if channel_filters:
                try:
                    sql_query = _build_channel_sql(channel_filters)
                    rows = _run_query(sql_query, channel_filters)
                    rows = _convert_rows_to_containers(rows)
                except Exception as db_error:
                    rows = _load_base_rows_from_csv()
                    fallback_used = True
                    fallback_reason = (
                        f"静态快照缺失，且渠道实时查询不可用，已回退全量：{db_error}"
                    )
            else:
                try:
                    rows = _run_query(BASE_VOLUME_SQL)
                    rows = _convert_rows_to_containers(rows)
                except Exception as db_error:
                    rows = _load_base_rows_from_csv()
                    fallback_used = True
                    fallback_reason = f"静态快照缺失，回退 CSV：{db_error}"
        else:
            fallback_used = bool(channel_filters)
            fallback_reason = (
                f"已使用 {snapshot_date} 的静态快照数据"
                if snapshot_date
                else "已使用静态快照数据"
            )

        result = []
        total_volume = 0.0
        unmapped = []
        for row in rows:
            name = (_row_get(row, "WarehouseName", "") or "").strip()
            if _is_excluded_warehouse(name):
                continue
            volume = float(_row_get(row, "TotalOccupiedVolume", 0) or 0)
            meta = _get_warehouse_meta(name)
            coords = meta.get("coords")
            capacity = meta.get("capacity")
            if not coords:
                unmapped.append(name)
            result.append(
                {
                    "name": name,
                    "volume": volume,
                    "capacity": capacity,
                    "coords": coords,
                }
            )
            total_volume += volume

        return jsonify(
            {
                "status": "success",
                "filters": {"channels": channel_filters},
                "channelSqlConfigured": bool(CHANNEL_VOLUME_SQL),
                "data": result,
                "total": total_volume,
                "unmappedWarehouses": unmapped,
                "snapshotDate": snapshot_date,
                "fallback": {
                    "used": fallback_used,
                    "source": "snapshot" if snapshot_date else ("csv" if fallback_used else "database"),
                    "reason": fallback_reason,
                },
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/trend-data")
def get_trend_data():
    try:
        channel_filters = _parse_channel_filters()
        days = _parse_days()

        fallback_used = False
        fallback_reason = None
        source = "snapshot_daily"
        store = _load_snapshot_store()
        refreshed = False
        rows = _build_snapshot_trend(days, channel_filters, store)
        if not rows:
            fallback_used = True
            fallback_total = _csv_total_volume()
            rows = _build_flat_trend(days, fallback_total)
            source = "flat:csv_snapshot"
            fallback_reason = "静态快照为空，已退化为单值趋势"

        trend_data = []
        totals_by_date = {}
        channel_totals = {}

        for row in rows:
            if isinstance(row, dict) and "date" in row:
                date_text = row["date"]
                channel = row["channel"]
                volume = float(row["volume"])
            else:
                raw_date = _row_get(row, "VolumeDate")
                if hasattr(raw_date, "strftime"):
                    date_text = raw_date.strftime("%Y-%m-%d")
                else:
                    date_text = str(raw_date)
                channel = (_row_get(row, "ChannelName", "") or "").strip() or "UNK"
                volume = float(_row_get(row, "TotalOccupiedVolume", 0) or 0)

            trend_data.append(
                {"date": date_text, "channel": channel, "volume": volume}
            )
            totals_by_date[date_text] = totals_by_date.get(date_text, 0.0) + volume
            channel_totals[channel] = channel_totals.get(channel, 0.0) + volume

        trend_data.sort(key=lambda x: (x["date"], x["channel"]))
        totals = [
            {"date": d, "volume": totals_by_date[d]}
            for d in sorted(totals_by_date.keys())
        ]
        ranked_channels = sorted(
            [{"channel": k, "total": v} for k, v in channel_totals.items()],
            key=lambda x: x["total"],
            reverse=True,
        )

        return jsonify(
            {
                "status": "success",
                "filters": {"channels": channel_filters, "days": days},
                "data": trend_data,
                "totals": totals,
                "channelTotals": ranked_channels,
                "source": source,
                "fallback": {
                    "used": fallback_used,
                    "reason": fallback_reason,
                },
                "snapshot": {
                    "refreshedToday": refreshed,
                    "lastUpdated": store.get("last_updated"),
                },
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/snapshot-refresh")
def refresh_snapshot_legacy():
    try:
        force = request.args.get("force", "0") == "1"
        store, refreshed = _refresh_daily_snapshot(force=force)
        return jsonify(
            {
                "status": "success",
                "refreshed": refreshed,
                "lastUpdated": store.get("last_updated"),
                "days": len(store.get("days", [])),
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/snapshot/refresh")
def refresh_snapshot():
    try:
        force = request.args.get("force", "0") == "1"
        store, created = _refresh_daily_snapshot(force=force)
        latest = store.get("days", [])[-1] if store.get("days") else None
        return jsonify(
            {
                "status": "success",
                "created": created,
                "latestDate": latest.get("date") if latest else None,
                "source": latest.get("source") if latest else None,
                "daysStored": len(store.get("days", [])),
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/snapshot/status")
def snapshot_status():
    store = _load_snapshot_store()
    latest = store.get("days", [])[-1] if store.get("days") else None
    return jsonify(
        {
            "status": "success",
            "lastUpdated": store.get("last_updated"),
            "daysStored": len(store.get("days", [])),
            "latestDate": latest.get("date") if latest else None,
            "latestSource": latest.get("source") if latest else None,
        }
    )


@app.route("/channels")
def get_channels():
    try:
        if not CHANNEL_VOLUME_SQL:
            return jsonify({"status": "success", "configured": False, "data": []})

        sql = f"""
        WITH ChannelVolume AS (
            {CHANNEL_VOLUME_SQL}
        )
        SELECT DISTINCT ChannelName
        FROM ChannelVolume
        WHERE ChannelName IS NOT NULL AND LTRIM(RTRIM(ChannelName)) <> ''
        ORDER BY ChannelName
        """
        rows = _run_query(sql)
        return jsonify(
            {
                "status": "success",
                "configured": True,
                "data": [row.ChannelName for row in rows],
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)