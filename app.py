import json
import os
from datetime import datetime
from pathlib import Path
from statistics import mean

from flask import Flask, jsonify, request, send_file

try:
    import pyodbc
except ImportError:
    pyodbc = None

app = Flask(__name__)

ROOT_DIR = Path(__file__).parent
QUERY_FILE = ROOT_DIR / "dashboard_queries.json"

CONTAINER_VOLUME_M3 = 69.0
DEFAULT_CONN_STR = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    "SERVER=if-akl-live.database.windows.net;"
    "DATABASE=nz_ierp_live;"
    "UID=nzlivepooluser;"
    "PWD=iFur3RP@5sc^l^t3!;"
)

QUERY_KEYS = [
    "supply_availability",
    "store_display_rate",
    "warehouse_capacity",
    "in_transit_by_channel",
    "forecast_in_transit",
]

DEFAULT_QUERY_CONFIG = {
    "supply_availability": (
        "-- TODO_SQL: non-discontinue 产品各渠道有货率\n"
        "-- REQUIRED COLUMNS: channel, in_stock_rate\n"
        "SELECT TOP 1 'Retail' AS channel, 0.95 AS in_stock_rate;"
    ),
    "store_display_rate": (
        "-- TODO_SQL: non-discontinue 产品店面展示率\n"
        "-- REQUIRED COLUMNS: channel, display_rate\n"
        "SELECT TOP 1 'Retail' AS channel, 0.88 AS display_rate;"
    ),
    "warehouse_capacity": (
        "-- TODO_SQL: 仓库容积（占用与总容量）\n"
        "-- REQUIRED COLUMNS: warehouse, used_volume_m3, total_capacity_m3\n"
        "SELECT TOP 1 'Main Warehouse' AS warehouse, 5000.0 AS used_volume_m3, 8000.0 AS total_capacity_m3;"
    ),
    "in_transit_by_channel": (
        "-- TODO_SQL: 各渠道在途（推荐返回货柜数）\n"
        "-- REQUIRED COLUMNS: channel, in_transit_containers (or in_transit_m3)\n"
        "SELECT TOP 1 'Retail' AS channel, 23.0 AS in_transit_containers;"
    ),
    "forecast_in_transit": (
        "-- TODO_SQL: 在途预计趋势\n"
        "-- REQUIRED COLUMNS: forecast_date, expected_containers (or expected_m3), channel(optional)\n"
        "SELECT CAST(GETDATE() AS date) AS forecast_date, 25.0 AS expected_containers, 'Retail' AS channel;"
    ),
}

SAMPLE_METRICS = {
    "supply_availability": [
        {"channel": "Retail", "in_stock_rate": 94.2},
        {"channel": "Online", "in_stock_rate": 89.4},
        {"channel": "Wholesale", "in_stock_rate": 92.7},
    ],
    "store_display_rate": [
        {"channel": "Retail", "display_rate": 86.3},
        {"channel": "Franchise", "display_rate": 82.1},
        {"channel": "Mall", "display_rate": 88.4},
    ],
    "warehouse_capacity": [
        {"warehouse": "Auckland Main", "used_containers": 82.4, "total_containers": 120.0},
        {"warehouse": "Christchurch", "used_containers": 54.1, "total_containers": 80.0},
        {"warehouse": "Hamilton", "used_containers": 31.8, "total_containers": 50.0},
    ],
    "in_transit_by_channel": [
        {"channel": "Retail", "in_transit_qty": 21.0},
        {"channel": "Online", "in_transit_qty": 16.0},
        {"channel": "Wholesale", "in_transit_qty": 12.0},
    ],
    "forecast_in_transit": [
        {"date": "2026-08-25", "expected_qty": 18.0, "channel": "Retail"},
        {"date": "2026-08-26", "expected_qty": 22.0, "channel": "Retail"},
        {"date": "2026-08-27", "expected_qty": 26.0, "channel": "Retail"},
        {"date": "2026-08-28", "expected_qty": 19.0, "channel": "Retail"},
        {"date": "2026-08-29", "expected_qty": 23.0, "channel": "Retail"},
    ],
}


def _ensure_query_file():
    if not QUERY_FILE.exists():
        with QUERY_FILE.open("w", encoding="utf-8") as f:
            json.dump(DEFAULT_QUERY_CONFIG, f, ensure_ascii=False, indent=2)
        return

    with QUERY_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        data = {}

    changed = False
    for key, sql in DEFAULT_QUERY_CONFIG.items():
        if key not in data:
            data[key] = sql
            changed = True

    if changed:
        with QUERY_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _load_query_config():
    _ensure_query_file()
    with QUERY_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return dict(DEFAULT_QUERY_CONFIG)
    out = dict(DEFAULT_QUERY_CONFIG)
    out.update({k: str(v) for k, v in data.items() if isinstance(v, str)})
    return out


def _conn_str():
    raw = os.getenv("WAREHOUSE_DB_CONN_STR") or os.getenv("AZURE_SQL_CONN_STR") or DEFAULT_CONN_STR
    if pyodbc is not None and "ODBC Driver 17 for SQL Server" in raw:
        try:
            if "ODBC Driver 18 for SQL Server" in pyodbc.drivers():
                return raw.replace("ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server")
        except Exception:
            pass
    return raw


def _conn_info():
    parts = {}
    for token in _conn_str().split(";"):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        parts[key.strip().upper()] = value.strip()
    return {
        "server": parts.get("SERVER"),
        "database": parts.get("DATABASE"),
        "driver": parts.get("DRIVER"),
    }


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _pick(row, keys):
    lower = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        if key in lower and lower[key] not in (None, ""):
            return lower[key]
    return None


def _as_percent(value):
    v = _to_float(value)
    if v is None:
        return None
    if v <= 1:
        v = v * 100
    return max(0.0, min(v, 100.0))


def _m3_to_containers(value):
    v = _to_float(value)
    if v is None:
        return None
    return v / CONTAINER_VOLUME_M3


def _normalize_supply(rows):
    buckets = {}
    for row in rows:
        channel = _pick(row, ["channel", "channelname", "channelcode", "channel_code"])
        rate = _pick(row, ["in_stock_rate", "availability_rate", "in_stock_pct", "in_stock_percent"])
        rate = _as_percent(rate)
        if not channel or rate is None:
            continue
        key = str(channel).strip()
        buckets.setdefault(key, []).append(rate)
    return [
        {"channel": ch, "in_stock_rate": round(mean(values), 2)}
        for ch, values in sorted(buckets.items())
    ]


def _normalize_store(rows):
    buckets = {}
    for row in rows:
        channel = _pick(row, ["channel", "channelname", "store_channel", "channelcode"])
        rate = _pick(row, ["display_rate", "display_pct", "display_percent"])
        rate = _as_percent(rate)
        if not channel or rate is None:
            continue
        key = str(channel).strip()
        buckets.setdefault(key, []).append(rate)
    return [
        {"channel": ch, "display_rate": round(mean(values), 2)}
        for ch, values in sorted(buckets.items())
    ]


def _normalize_warehouse(rows):
    out = []
    for row in rows:
        warehouse = _pick(row, ["warehouse", "warehousename", "warehouse_name", "name"])
        if not warehouse:
            continue

        used = _to_float(_pick(row, ["used_containers", "occupied_containers", "used_container", "occupied_container"]))
        total = _to_float(_pick(row, ["total_containers", "capacity_containers", "total_container", "capacity_container"]))

        if used is None:
            used = _m3_to_containers(_pick(row, ["used_volume_m3", "occupied_volume_m3", "used_m3", "occupied_m3"]))
        if total is None:
            total = _m3_to_containers(_pick(row, ["total_capacity_m3", "capacity_m3", "total_m3"]))

        if used is None:
            used = _to_float(_pick(row, ["used_volume", "occupied_volume", "used", "occupied"]))
        if total is None:
            total = _to_float(_pick(row, ["total_capacity", "capacity", "total"]))

        if used is None and total is None:
            continue

        used = used or 0.0
        total = total or 0.0
        occupancy_rate = (used / total * 100.0) if total > 0 else 0.0
        out.append(
            {
                "warehouse": str(warehouse).strip(),
                "used_containers": round(used, 2),
                "total_containers": round(total, 2),
                "occupancy_rate": round(occupancy_rate, 2),
            }
        )
    out.sort(key=lambda item: item["occupancy_rate"], reverse=True)
    return out


def _normalize_in_transit(rows):
    buckets = {}
    for row in rows:
        channel = _pick(row, ["channel", "channelname", "channelcode"])
        qty = _to_float(_pick(row, ["in_transit_qty", "in_transit_containers", "in_transit"]))
        if qty is None:
            qty = _m3_to_containers(_pick(row, ["in_transit_m3", "in_transit_volume_m3"]))
        if not channel or qty is None:
            continue
        key = str(channel).strip()
        buckets[key] = buckets.get(key, 0.0) + qty
    out = [{"channel": ch, "in_transit_qty": round(v, 2)} for ch, v in buckets.items()]
    out.sort(key=lambda item: item["in_transit_qty"], reverse=True)
    return out


def _normalize_forecast(rows):
    out = []
    for row in rows:
        raw_date = _pick(row, ["forecast_date", "date", "eta_date", "day"])
        if raw_date is None:
            continue
        if hasattr(raw_date, "strftime"):
            date_text = raw_date.strftime("%Y-%m-%d")
        else:
            date_text = str(raw_date)[:10]

        qty = _to_float(_pick(row, ["expected_qty", "expected_containers", "forecast_qty"]))
        if qty is None:
            qty = _m3_to_containers(_pick(row, ["expected_m3", "forecast_m3"]))
        if qty is None:
            continue

        channel = _pick(row, ["channel", "channelname", "channelcode"])
        out.append(
            {
                "date": date_text,
                "expected_qty": round(qty, 2),
                "channel": str(channel).strip() if channel else "ALL",
            }
        )
    out.sort(key=lambda item: (item["date"], item["channel"]))
    return out


def _parse_channels():
    channels = request.args.getlist("channel")
    if not channels:
        channels_csv = request.args.get("channels", "")
        channels = channels_csv.split(",") if channels_csv else []
    return [c.strip() for c in channels if c and c.strip()]


def _filter_channels(rows, channels, key):
    if not channels:
        return rows
    wanted = {c.upper() for c in channels}
    out = []
    for row in rows:
        value = str(row.get(key, "")).strip().upper()
        if value in wanted:
            out.append(row)
    return out


def _is_placeholder_sql(sql):
    text = (sql or "").strip().upper()
    if not text:
        return True
    return "TODO_SQL" in text


def _run_query(sql, params=None):
    if pyodbc is None:
        raise RuntimeError("pyodbc 不可用，请安装 ODBC 运行环境。")
    conn = None
    cursor = None
    try:
        conn = pyodbc.connect(_conn_str(), timeout=15)
        cursor = conn.cursor()
        cursor.execute(sql, params or [])
        columns = [col[0] for col in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _metric_result(metric_key, normalizer, channels):
    config = _load_query_config()
    sql = config.get(metric_key, "")

    if _is_placeholder_sql(sql):
        rows = SAMPLE_METRICS[metric_key]
        if metric_key in ("supply_availability", "store_display_rate", "in_transit_by_channel"):
            rows = _filter_channels(rows, channels, "channel")
        if metric_key == "forecast_in_transit":
            rows = _filter_channels(rows, channels, "channel")
        return {
            "source": "sample",
            "queryConfigured": False,
            "rows": rows,
            "warning": f"{metric_key} SQL 未配置，当前展示示例数据。",
            "error": None,
        }

    try:
        rows = normalizer(_run_query(sql))
        if metric_key in ("supply_availability", "store_display_rate", "in_transit_by_channel"):
            rows = _filter_channels(rows, channels, "channel")
        if metric_key == "forecast_in_transit":
            rows = _filter_channels(rows, channels, "channel")
        return {
            "source": "database",
            "queryConfigured": True,
            "rows": rows,
            "warning": None,
            "error": None,
        }
    except Exception as e:
        return {
            "source": "error",
            "queryConfigured": True,
            "rows": [],
            "warning": None,
            "error": str(e),
        }


def _build_summary(metrics):
    supply_rows = metrics["supply_availability"]["rows"]
    store_rows = metrics["store_display_rate"]["rows"]
    wh_rows = metrics["warehouse_capacity"]["rows"]
    transit_rows = metrics["in_transit_by_channel"]["rows"]
    forecast_rows = metrics["forecast_in_transit"]["rows"]

    supply_avg = mean([r["in_stock_rate"] for r in supply_rows]) if supply_rows else None
    store_avg = mean([r["display_rate"] for r in store_rows]) if store_rows else None
    used_total = sum(r["used_containers"] for r in wh_rows)
    cap_total = sum(r["total_containers"] for r in wh_rows)
    in_transit_total = sum(r["in_transit_qty"] for r in transit_rows)
    forecast_total = sum(r["expected_qty"] for r in forecast_rows)

    return {
        "supply_in_stock_rate": round(supply_avg, 2) if supply_avg is not None else None,
        "store_display_rate": round(store_avg, 2) if store_avg is not None else None,
        "warehouse_used_containers": round(used_total, 2),
        "warehouse_total_containers": round(cap_total, 2),
        "warehouse_occupancy_rate": round((used_total / cap_total * 100.0), 2) if cap_total > 0 else None,
        "in_transit_total": round(in_transit_total, 2),
        "forecast_total": round(forecast_total, 2),
    }


def _collect_diagnostics(metrics):
    messages = []
    for key, data in metrics.items():
        if data.get("error"):
            messages.append(
                {
                    "metric": key,
                    "level": "error",
                    "message": data["error"],
                }
            )
        elif data.get("warning"):
            messages.append(
                {
                    "metric": key,
                    "level": "warning",
                    "message": data["warning"],
                }
            )
    return messages


@app.route("/")
def index():
    return send_file(ROOT_DIR / "index.html")


@app.route("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "time": datetime.utcnow().isoformat() + "Z",
            "queryConfigFile": str(QUERY_FILE),
        }
    )


@app.route("/api/query-config")
def query_config():
    config = _load_query_config()
    return jsonify(
        {
            "status": "success",
            "file": str(QUERY_FILE),
            "queries": {
                key: {
                    "configured": not _is_placeholder_sql(config.get(key, "")),
                    "preview": config.get(key, "")[:180],
                }
                for key in QUERY_KEYS
            },
        }
    )


@app.route("/api/connection-test")
def connection_test():
    info = _conn_info()
    drivers = []
    try:
        drivers = pyodbc.drivers() if pyodbc is not None else []
    except Exception:
        drivers = []

    if pyodbc is None:
        return jsonify(
            {
                "status": "error",
                "message": "pyodbc 导入失败，ODBC 运行环境不可用。",
                "connection": info,
                "drivers": drivers,
            }
        )

    try:
        rows = _run_query("SELECT 1 AS ok")
        return jsonify(
            {
                "status": "success",
                "message": "数据库连通正常。",
                "connection": info,
                "drivers": drivers,
                "probe": rows[0] if rows else {"ok": 1},
            }
        )
    except Exception as e:
        return jsonify(
            {
                "status": "error",
                "message": str(e),
                "connection": info,
                "drivers": drivers,
            }
        )


@app.route("/api/metrics")
def metrics():
    channels = _parse_channels()
    data = {
        "supply_availability": _metric_result("supply_availability", _normalize_supply, channels),
        "store_display_rate": _metric_result("store_display_rate", _normalize_store, channels),
        "warehouse_capacity": _metric_result("warehouse_capacity", _normalize_warehouse, channels),
        "in_transit_by_channel": _metric_result("in_transit_by_channel", _normalize_in_transit, channels),
        "forecast_in_transit": _metric_result("forecast_in_transit", _normalize_forecast, channels),
    }

    available_channels = sorted(
        {
            str(item.get("channel")).strip()
            for key in ("supply_availability", "store_display_rate", "in_transit_by_channel")
            for item in data[key]["rows"]
            if item.get("channel")
        }
    )

    return jsonify(
        {
            "status": "success",
            "asOf": datetime.utcnow().isoformat() + "Z",
            "filters": {"channels": channels},
            "channels": available_channels,
            "summary": _build_summary(data),
            "metrics": data,
            "diagnostics": _collect_diagnostics(data),
        }
    )


if __name__ == "__main__":
    _ensure_query_file()
    app.run(host="0.0.0.0", port=5000, debug=True)
