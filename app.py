import csv
import os
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request
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

# 3) 渠道体积 SQL 占位（你给我 SQL 后，把它粘贴到这里）
# 必须输出三列：
#   WarehouseName | ChannelName | TotalOccupiedVolume
# 例如：
# SELECT w.Name AS WarehouseName, o.Channel AS ChannelName,
#        SUM(ol.Quantity * ISNULL(p.VolumeWithBox, 0)) AS TotalOccupiedVolume
# FROM ...
# GROUP BY w.Name, o.Channel
CHANNEL_VOLUME_SQL = None


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
            meta[name] = {
                "coords": _parse_coords(row.get("zuobiao")),
                "capacity": float(capacity_raw) if capacity_raw not in (None, "") else None,
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
            volume_raw = row.get("TotalOccupiedVolume")
            volume = float(volume_raw) if volume_raw not in (None, "") else 0.0
            rows.append(
                {
                    "WarehouseName": name,
                    "TotalOccupiedVolume": volume,
                }
            )
    rows.sort(key=lambda item: item["TotalOccupiedVolume"], reverse=True)
    return rows


def _normalize_name(name):
    return "".join(ch.lower() for ch in str(name) if ch.isalnum())


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


try:
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
except FileNotFoundError:
    html_content = "<h1>Error: index.html not found</h1>"


@app.route("/")
def index():
    return render_template_string(html_content)


@app.route("/get-data")
def get_data():
    try:
        channel_filters = _parse_channel_filters()
        fallback_used = False
        fallback_reason = None

        if channel_filters:
            sql_query = _build_channel_sql(channel_filters)
            rows = _run_query(sql_query, channel_filters)
        else:
            try:
                rows = _run_query(BASE_VOLUME_SQL)
            except Exception as db_error:
                # 无数据库驱动或数据库不可达时，回退到 CSV，保证地图可用。
                rows = _load_base_rows_from_csv()
                fallback_used = True
                fallback_reason = str(db_error)

        result = []
        total_volume = 0.0
        unmapped = []
        for row in rows:
            name = (_row_get(row, "WarehouseName", "") or "").strip()
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
                "fallback": {
                    "used": fallback_used,
                    "source": "csv" if fallback_used else "database",
                    "reason": fallback_reason,
                },
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


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