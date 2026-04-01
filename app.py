import csv
import os
from datetime import date, timedelta
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


def _csv_total_volume():
    return sum(item["TotalOccupiedVolume"] for item in _load_base_rows_from_csv())


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


def _detect_stocks_date_column():
    sql = """
    SELECT c.name AS ColumnName
    FROM sys.columns c
    JOIN sys.tables t ON c.object_id = t.object_id
    JOIN sys.schemas s ON t.schema_id = s.schema_id
    JOIN sys.types ty ON c.user_type_id = ty.user_type_id
    WHERE s.name = 'dbo'
      AND t.name = 'Stocks'
      AND ty.name IN ('date', 'datetime', 'datetime2', 'smalldatetime')
    """
    rows = _run_query(sql)
    candidates = [_row_get(row, "ColumnName", "") for row in rows]
    candidates_lower = {c.lower(): c for c in candidates}
    priority = [
        "businessdate",
        "snapshotdate",
        "stockdate",
        "updatedon",
        "updatetime",
        "modifiedon",
        "modifiedtime",
        "createdon",
        "createtime",
        "createon",
    ]
    for item in priority:
        if item in candidates_lower:
            return candidates_lower[item]
    return candidates[0] if candidates else None


def _build_daily_trend_sql(channel_filters, days):
    params = [days]
    channel_where = ""
    if channel_filters:
        placeholders = ",".join("?" for _ in channel_filters)
        channel_where = f"AND ChannelName IN ({placeholders})"
        params.extend(channel_filters)

    if DAILY_CHANNEL_TREND_SQL:
        sql = f"""
        WITH DailyChannel AS (
            {DAILY_CHANNEL_TREND_SQL}
        )
        SELECT
            CAST(VolumeDate AS date) AS VolumeDate,
            ChannelName,
            SUM(TotalOccupiedVolume) AS TotalOccupiedVolume
        FROM DailyChannel
        WHERE CAST(VolumeDate AS date) >= DATEADD(day, -?, CAST(GETDATE() AS date))
          {channel_where}
        GROUP BY CAST(VolumeDate AS date), ChannelName
        ORDER BY VolumeDate, ChannelName
        """
        return sql, params, "custom_sql"

    date_column = _detect_stocks_date_column()
    if not date_column:
        raise RuntimeError(
            "未找到 dbo.Stocks 的日期字段，请配置 DAILY_CHANNEL_TREND_SQL。"
        )
    date_expr = f"CAST(s.[{date_column}] AS date)"
    sql = f"""
    WITH DailyChannel AS (
        SELECT
            {date_expr} AS VolumeDate,
            LEFT(p.SKU, 3) AS ChannelName,
            SUM(s.Quantity * ISNULL(p.VolumeWithBox, 0)) AS TotalOccupiedVolume
        FROM dbo.Stocks s
        LEFT JOIN dbo.Products p ON s.ProductId = p.Id
        WHERE p.SKU IS NOT NULL
          AND s.[{date_column}] IS NOT NULL
          AND {date_expr} >= DATEADD(day, -?, CAST(GETDATE() AS date))
        GROUP BY {date_expr}, LEFT(p.SKU, 3)
    )
    SELECT
        VolumeDate,
        ChannelName,
        SUM(TotalOccupiedVolume) AS TotalOccupiedVolume
    FROM DailyChannel
    WHERE 1=1
      {channel_where}
    GROUP BY VolumeDate, ChannelName
    ORDER BY VolumeDate, ChannelName
    """
    return sql, params, f"stocks.{date_column}"


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
            try:
                sql_query = _build_channel_sql(channel_filters)
                rows = _run_query(sql_query, channel_filters)
            except Exception as db_error:
                return jsonify(
                    {
                        "status": "error",
                        "message": (
                            "渠道筛选需要数据库查询支持，当前不可用："
                            f"{db_error}"
                        ),
                    }
                )
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


@app.route("/trend-data")
def get_trend_data():
    try:
        channel_filters = _parse_channel_filters()
        days = _parse_days()

        fallback_used = False
        fallback_reason = None
        source = "database"
        rows = []
        try:
            trend_sql, params, source = _build_daily_trend_sql(channel_filters, days)
            rows = _run_query(trend_sql, params)
        except Exception as db_error:
            fallback_used = True
            fallback_reason = str(db_error)
            if channel_filters:
                return jsonify(
                    {
                        "status": "error",
                        "message": (
                            "按渠道查询每日趋势需要数据库支持，当前不可用："
                            f"{db_error}"
                        ),
                    }
                )
            rows = _build_flat_trend(days, _csv_total_volume())
            source = "csv_flat"

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