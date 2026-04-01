import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path
from threading import Lock

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

DAILY_CHANNEL_TREND_SQL = None
SNAPSHOT_FILE = Path(__file__).with_name("data").joinpath("daily_snapshots.json")
MASTER_FILE = Path(__file__).with_name("warehouse_master.csv")
CONTAINER_VOLUME_M3 = 69.0
MASTER_LOCK = Lock()

# 默认不参与体积统计的仓库
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
def _row_get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _normalize_warehouse_key(name):
    return "".join(ch.lower() for ch in str(name) if ch.isalnum())


EXCLUDED_WAREHOUSE_KEYS = {
    _normalize_warehouse_key(name) for name in EXCLUDED_WAREHOUSE_NAMES
}


EXCLUDED_WAREHOUSE_KEYS = {
    _normalize_warehouse_key(name) for name in EXCLUDED_WAREHOUSE_NAMES
}


def _parse_bool(value, default=True):
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return default


def _m3_to_container(value):
    return float(value or 0) / CONTAINER_VOLUME_M3


def _parse_coords(coords_text):
    # 输入格式 "lat, lng"；返回 [lng, lat]
    if not coords_text:
        return None
    parts = [p.strip() for p in str(coords_text).split(",")]
    if len(parts) != 2:
        return None
    lat = float(parts[0])
    lng = float(parts[1])
    return [lng, lat]


def _format_coords_for_csv(coords):
    if not isinstance(coords, list) or len(coords) != 2:
        return ""
    return f"{coords[1]}, {coords[0]}"


def _load_seed_from_data_csv():
    seed = {}
    csv_path = Path(__file__).with_name("data.csv")
    if not csv_path.exists():
        return seed

    with csv_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("WarehouseName") or "").strip()
            if not name:
                continue
            cap_raw = row.get("Capacity")
            cap_m3 = float(cap_raw) if cap_raw not in (None, "") else None
            seed[name] = {
                "coords": _parse_coords(row.get("zuobiao")),
                "include_in_volume": True,
                "capacity": _m3_to_container(cap_m3) if cap_m3 is not None else None,
            }
    return seed


def _save_warehouse_master(master):
    with MASTER_FILE.open("w", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["WarehouseName", "Coords", "IncludeInVolume", "CapacityM3"])
        for name in sorted(master.keys()):
            item = master[name]
            include = "1" if item.get("include_in_volume", True) else "0"
            capacity_containers = item.get("capacity")
            if capacity_containers is None:
                cap_m3_text = ""
            else:
                cap_m3_text = f"{float(capacity_containers) * CONTAINER_VOLUME_M3:.6f}".rstrip("0").rstrip(".")
            writer.writerow(
                [
                    name,
                    _format_coords_for_csv(item.get("coords")),
                    include,
                    cap_m3_text,
                ]
            )


def _load_warehouse_master():
    master = {}
    if not MASTER_FILE.exists():
        return master

    with MASTER_FILE.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("WarehouseName") or "").strip()
            if not name:
                continue
            include = _parse_bool(row.get("IncludeInVolume"), default=True)
            coords = _parse_coords(row.get("Coords"))
            cap_raw = row.get("CapacityM3")
            cap_m3 = float(cap_raw) if cap_raw not in (None, "") else None
            master[name] = {
                "coords": coords,
                "include_in_volume": include,
                "capacity": _m3_to_container(cap_m3) if cap_m3 is not None else None,
            }
    return master


def _ensure_warehouse_master():
    master = _load_warehouse_master()
    if not master:
        master = _load_seed_from_data_csv()
        for name in EXCLUDED_WAREHOUSE_NAMES:
            if name not in master:
                master[name] = {
                    "coords": None,
                    "include_in_volume": False,
                    "capacity": None,
                }
            else:
                master[name]["include_in_volume"] = False
        if master:
            _save_warehouse_master(master)
        return master

    # master 已存在，但保证排除仓库都在表里
    changed = False
    for name in EXCLUDED_WAREHOUSE_NAMES:
        if name not in master:
            master[name] = {
                "coords": None,
                "include_in_volume": False,
                "capacity": None,
            }
            changed = True
    if changed:
        _save_warehouse_master(master)
    return master


WAREHOUSE_MASTER = _ensure_warehouse_master()


def _master_entry(name):
    if not name:
        return None
    if name in WAREHOUSE_MASTER:
        return WAREHOUSE_MASTER[name]
    target = _normalize_warehouse_key(name)
    for key, val in WAREHOUSE_MASTER.items():
        n = _normalize_warehouse_key(key)
        if n == target:
            return val
    return None


def _is_excluded_warehouse(name):
    entry = _master_entry(name)
    if entry is None:
        return _normalize_warehouse_key(name) in EXCLUDED_WAREHOUSE_KEYS
    return not bool(entry.get("include_in_volume", False))


def _warehouse_meta(name):
    entry = _master_entry(name)
    if entry is None:
        include = _normalize_warehouse_key(name) not in EXCLUDED_WAREHOUSE_KEYS
        return {
            "coords": None,
            "capacity": None,
            "includeInVolume": include,
        }
    return {
        "coords": entry.get("coords"),
        "capacity": entry.get("capacity"),
        "includeInVolume": bool(entry.get("include_in_volume", False)),
    }


def _warehouse_master_profiles(extra_names=None):
    names = set(WAREHOUSE_MASTER.keys())
    if extra_names:
        for n in extra_names:
            if n:
                names.add(n)
    profiles = []
    for name in sorted(names):
        meta = _warehouse_meta(name)
        profiles.append(
            {
                "name": name,
                "coords": meta["coords"],
                "includeInVolume": meta["includeInVolume"],
            }
        )
    return profiles


def _canonical_master_name(name):
    if name in WAREHOUSE_MASTER:
        return name
    target = _normalize_warehouse_key(name)
    if not target:
        return name
    best = None
    best_score = None
    for key in WAREHOUSE_MASTER.keys():
        normalized = _normalize_warehouse_key(key)
        if normalized == target:
            return key
        if normalized and (target in normalized or normalized in target):
            score = abs(len(normalized) - len(target))
            if best is None or score < best_score:
                best = key
                best_score = score
    return best or name


def _parse_optional_float(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return float(value)


def _parse_update_coords(payload):
    lat = _parse_optional_float(payload.get("lat"))
    lng = _parse_optional_float(payload.get("lng"))
    if lat is None and lng is None:
        return None
    if lat is None or lng is None:
        raise ValueError("坐标需同时提供纬度和经度，或同时留空。")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("坐标超出合法范围。")
    return [lng, lat]


def _parse_coords_payload(value):
    """支持 None / [lng, lat] / {'lat': x, 'lng': y} / 'lat,lng'。"""
    if value is None:
        return None

    if isinstance(value, list):
        if len(value) != 2:
            raise ValueError("坐标数组格式应为 [lng, lat]。")
        lng = _parse_optional_float(value[0])
        lat = _parse_optional_float(value[1])
    elif isinstance(value, dict):
        lat = _parse_optional_float(value.get("lat"))
        lng = _parse_optional_float(value.get("lng"))
    elif isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        parsed = _parse_coords(text)
        if parsed is None:
            raise ValueError("坐标字符串格式应为 lat,lng。")
        lng, lat = parsed[0], parsed[1]
    else:
        raise ValueError("不支持的坐标类型。")

    if lat is None or lng is None:
        raise ValueError("坐标需同时提供纬度和经度。")
    if not (-90 <= float(lat) <= 90 and -180 <= float(lng) <= 180):
        raise ValueError("坐标超出合法范围。")
    return [float(lng), float(lat)]


def _save_profiles_batch(profiles):
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profiles 必须是非空数组。")

    for item in profiles:
        if not isinstance(item, dict):
            raise ValueError("profiles 中每一项必须是对象。")
        name = (item.get("name") or "").strip()
        if not name:
            raise ValueError("仓库名称不能为空。")
        canonical_name = _canonical_master_name(name)
        existing = WAREHOUSE_MASTER.get(canonical_name, {})

        if "coords" in item:
            coords = _parse_coords_payload(item.get("coords"))
        else:
            coords = existing.get("coords")

        if "includeInVolume" in item:
            include = _parse_bool(item.get("includeInVolume"), default=True)
        else:
            include = bool(existing.get("include_in_volume", True))

        WAREHOUSE_MASTER[canonical_name] = {
            "coords": coords,
            "include_in_volume": include,
            "capacity": existing.get("capacity"),
        }

    _save_warehouse_master(WAREHOUSE_MASTER)


def _convert_row_to_containers(row):
    if isinstance(row, dict):
        mapped = dict(row)
        mapped["TotalOccupiedVolume"] = _m3_to_container(mapped.get("TotalOccupiedVolume", 0))
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
    out.TotalOccupiedVolume = _m3_to_container(_row_get(row, "TotalOccupiedVolume", 0))
    return out


def _convert_rows_to_containers(rows):
    return [_convert_row_to_containers(row) for row in rows]


def _load_base_rows_from_csv():
    rows = []
    csv_path = Path(__file__).with_name("data.csv")
    if not csv_path.exists():
        return rows
    with csv_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("WarehouseName") or "").strip()
            if not name or _is_excluded_warehouse(name):
                continue
            volume_raw = row.get("TotalOccupiedVolume")
            rows.append(
                {
                    "WarehouseName": name,
                    "TotalOccupiedVolume": _m3_to_container(float(volume_raw) if volume_raw not in (None, "") else 0.0),
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
        if not name or _is_excluded_warehouse(name):
            continue
        mapped[name] = _m3_to_container(_row_get(row, "TotalOccupiedVolume", 0) or 0)
    return mapped


def _snapshot_channel_rows_to_map(rows):
    mapped = {}
    for row in rows:
        channel = (_row_get(row, "ChannelName", "") or "").strip()
        warehouse = (_row_get(row, "WarehouseName", "") or "").strip()
        volume = _m3_to_container(_row_get(row, "TotalOccupiedVolume", 0) or 0)
        if not channel or not warehouse or _is_excluded_warehouse(warehouse):
            continue
        channel_key = channel.upper()
        if channel_key not in mapped:
            mapped[channel_key] = {}
        mapped[channel_key][warehouse] = volume
    return mapped


def _snapshot_total_by_channel(channel_filters, channel_map):
    if not channel_filters:
        return sum(sum(float(v) for v in warehouses.values()) for warehouses in channel_map.values())
    total = 0.0
    matched = False
    for channel in channel_filters:
        warehouses = channel_map.get(channel.upper(), {})
        if warehouses:
            matched = True
            total += sum(float(v) for v in warehouses.values())
    # 渠道未命中时返回 0，避免把 ALL 误当成渠道结果
    # 导致“地图显示0但总量/趋势仍有值”的口径错乱。
    if not matched:
        return 0.0
    return total


def _snapshot_map_rows(channel_filters, base_map, channel_map):
    if not channel_filters:
        if base_map:
            return [{"WarehouseName": name, "TotalOccupiedVolume": volume} for name, volume in base_map.items()]
        if "ALL" in channel_map:
            return [{"WarehouseName": name, "TotalOccupiedVolume": volume} for name, volume in channel_map["ALL"].items()]
        return []
    aggregate = {}
    for channel in channel_filters:
        for name, volume in channel_map.get(channel.upper(), {}).items():
            aggregate[name] = aggregate.get(name, 0.0) + float(volume)
    if aggregate:
        return [{"WarehouseName": name, "TotalOccupiedVolume": volume} for name, volume in aggregate.items()]
    # 渠道未命中时返回空集合，而不是回退 ALL
    # 这样地图和趋势都能准确反映“该渠道当前无数据”。
    if channel_filters:
        return []
    if "ALL" in channel_map:
        return [{"WarehouseName": name, "TotalOccupiedVolume": volume} for name, volume in channel_map["ALL"].items()]
    return []


def _build_snapshot_trend(days, channel_filters, store):
    items = store.get("days", [])
    if not items:
        return []
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
    today = date.today().isoformat()
    store = _load_snapshot_store()
    if not force and store.get("days") and store["days"][-1].get("date") == today:
        return store, False

    source = "database"
    try:
        base_rows = _convert_rows_to_containers(_run_query(BASE_VOLUME_SQL))
    except Exception:
        source = "csv"
        base_rows = _load_base_rows_from_csv()

    base_map = _snapshot_rows_to_map(base_rows)
    channel_map = {}
    if source == "database":
        try:
            channel_rows = _convert_rows_to_containers(_run_query(CHANNEL_VOLUME_SQL))
            channel_map = _snapshot_channel_rows_to_map(channel_rows)
        except Exception:
            channel_map = {}
    if not channel_map:
        channel_map = {"ALL": base_map}

    store["days"] = [item for item in store.get("days", []) if item.get("date") != today]
    store["days"].append(
        {
            "date": today,
            "source": source,
            "base": base_map,
            "channels": channel_map,
        }
    )
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
    channels = request.args.getlist("channel")
    if not channels:
        channels_csv = request.args.get("channels", "")
        channels = channels_csv.split(",") if channels_csv else []
    return [ch.strip() for ch in channels if ch and ch.strip()]


def _build_flat_trend(days, total):
    points = []
    end = date.today()
    for idx in range(days - 1, -1, -1):
        d = end - timedelta(days=idx)
        points.append({"date": d.isoformat(), "channel": "ALL", "volume": float(total)})
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
    channel_map = latest.get("channels", {}) if isinstance(latest.get("channels"), dict) else {}
    rows = _snapshot_map_rows(channel_filters, base_map, channel_map)
    rows.sort(key=lambda item: float(item.get("TotalOccupiedVolume", 0) or 0), reverse=True)
    return rows, latest.get("date")


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

        rows, snapshot_date = _rows_from_snapshot_store(channel_filters)
        if rows is None:
            if channel_filters:
                try:
                    rows = _convert_rows_to_containers(_run_query(_build_channel_sql(channel_filters), channel_filters))
                except Exception as db_error:
                    rows = _load_base_rows_from_csv()
                    fallback_used = True
                    fallback_reason = f"静态快照缺失，且渠道实时查询不可用，已回退全量：{db_error}"
            else:
                try:
                    rows = _convert_rows_to_containers(_run_query(BASE_VOLUME_SQL))
                except Exception as db_error:
                    rows = _load_base_rows_from_csv()
                    fallback_used = True
                    fallback_reason = f"静态快照缺失，回退 CSV：{db_error}"
        else:
            fallback_reason = f"已使用 {snapshot_date} 的静态快照数据" if snapshot_date else "已使用静态快照数据"

        rows_by_name = {}
        for row in rows:
            name = (_row_get(row, "WarehouseName", "") or "").strip()
            if not name:
                continue
            rows_by_name[name] = rows_by_name.get(name, 0.0) + float(_row_get(row, "TotalOccupiedVolume", 0) or 0)

        profiles = _warehouse_master_profiles(rows_by_name.keys())
        result = []
        total_volume = 0.0
        unmapped = []

        has_channel_filter = bool(channel_filters)
        for profile in profiles:
            name = profile["name"]
            include = bool(profile["includeInVolume"])
            coords = profile["coords"]
            volume = float(rows_by_name.get(name, 0.0)) if include else 0.0
            meta = _warehouse_meta(name)
            capacity = meta.get("capacity")
            if include:
                total_volume += volume
                if not coords:
                    unmapped.append(name)
                # 渠道筛选时仅展示命中仓库（volume>0），避免“全是0气泡”误导。
                should_show = bool(coords) and (not has_channel_filter or volume > 0)
                if should_show:
                    result.append(
                        {
                            "name": name,
                            "volume": volume,
                            "capacity": capacity,
                            "coords": coords,
                            "includeInVolume": True,
                            "displayOnMap": True,
                        }
                    )

        return jsonify(
            {
                "status": "success",
                "filters": {"channels": channel_filters},
                "channelSqlConfigured": bool(CHANNEL_VOLUME_SQL),
                "data": result,
                "total": total_volume,
                "warehouseProfiles": profiles,
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
            rows = _build_flat_trend(days, _csv_total_volume())
            source = "flat:csv_snapshot"
            fallback_reason = "静态快照为空，已退化为单值趋势"

        trend_data = []
        totals_by_date = {}
        channel_totals = {}

        for row in rows:
            date_text = row["date"] if isinstance(row, dict) and "date" in row else str(_row_get(row, "VolumeDate"))
            channel = row["channel"] if isinstance(row, dict) and "channel" in row else ((_row_get(row, "ChannelName", "") or "").strip() or "UNK")
            volume = float(row["volume"] if isinstance(row, dict) and "volume" in row else (_row_get(row, "TotalOccupiedVolume", 0) or 0))

            trend_data.append({"date": date_text, "channel": channel, "volume": volume})
            totals_by_date[date_text] = totals_by_date.get(date_text, 0.0) + volume
            channel_totals[channel] = channel_totals.get(channel, 0.0) + volume

        trend_data.sort(key=lambda x: (x["date"], x["channel"]))
        totals = [{"date": d, "volume": totals_by_date[d]} for d in sorted(totals_by_date.keys())]
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
                "fallback": {"used": fallback_used, "reason": fallback_reason},
                "snapshot": {"refreshedToday": refreshed, "lastUpdated": store.get("last_updated")},
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


@app.route("/warehouse-profile", methods=["POST"])
def update_warehouse_profile():
    try:
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        if not name:
            return jsonify({"status": "error", "message": "name 不能为空"}), 400

        profiles = [
            {
                "name": name,
                "coords": payload.get("coords"),
                "includeInVolume": payload.get("includeInVolume"),
            }
        ]
        _save_profiles_batch(profiles)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/warehouse-profiles", methods=["GET", "POST"])
def warehouse_profiles():
    if request.method == "GET":
        profiles = _warehouse_master_profiles()
        return jsonify({"status": "success", "total": len(profiles), "data": profiles})

    try:
        payload = request.get_json(silent=True) or {}
        profiles = payload.get("profiles")
        _save_profiles_batch(profiles)
        latest = _warehouse_master_profiles()
        return jsonify({"status": "success", "total": len(latest), "data": latest})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
