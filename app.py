import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

from flask import Flask, jsonify, request, send_file

try:
    import pyodbc
except ImportError:
    pyodbc = None

app = Flask(__name__)

ROOT_DIR = Path(__file__).parent
RUNNER_CONFIG_FILE = ROOT_DIR / "region_runner_config.json"

DEFAULT_REGION_CONFIG = {
    "NZ": {
        "label": "新西兰",
        "connection_uri": "mssql+pymssql://nzlivepooluser:iFur3RP%405sc%5El%5Et3%21@if-akl-live.database.windows.net:1433/nz_ierp_live?charset=utf8",
        "template_dir": "Data-NZ",
        "output_dir": "Output-NZ",
    },
    "AU": {
        "label": "澳洲",
        "connection_uri": "mssql+pymssql://appuserau:Ifurn1tureAuA7p5sc%5El%5Et@if-au-live.database.windows.net:1433/au_ierp_live?charset=utf8",
        "template_dir": "Data-AU",
        "output_dir": "Output-AU",
    },
    "CA": {
        "label": "加拿大",
        "connection_uri": "mssql+pymssql://capool:IfurnitureCA3sc%5El%5Et3@ca-sql-pool-server.database.windows.net:1433/ca_ierp_live?charset=utf8",
        "template_dir": "Data-CA",
        "output_dir": "Output-CA",
    },
}

DEFAULT_APP_SETTINGS = {
    "frequency_value": 30,
    "frequency_unit": "minute",
}


def _utc_iso():
    return datetime.utcnow().isoformat() + "Z"


def _ensure_default_template_dirs(regions):
    sample_sql = (
        "-- 这里写你的查询，文件后缀保持 .txt\n"
        "-- 示例：SELECT TOP 10 * FROM dbo.Warehouses;\n"
        "SELECT GETDATE() AS run_time;"
    )
    for cfg in regions.values():
        template_dir = _resolve_path(cfg.get("template_dir"))
        if template_dir is None:
            continue
        template_dir.mkdir(parents=True, exist_ok=True)
        has_txt = any(p.is_file() and p.suffix.lower() == ".txt" for p in template_dir.iterdir())
        if not has_txt:
            sample_file = template_dir / "example_query.txt"
            sample_file.write_text(sample_sql, encoding="utf-8")


def _ensure_runner_config():
    if not RUNNER_CONFIG_FILE.exists():
        payload = {"regions": DEFAULT_REGION_CONFIG, "settings": DEFAULT_APP_SETTINGS}
        with RUNNER_CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        _ensure_default_template_dirs(payload["regions"])
        return

    data = _load_runner_config()
    changed = False
    regions = data.get("regions", {})
    settings = data.get("settings", {})

    for region_key, region_config in DEFAULT_REGION_CONFIG.items():
        if region_key not in regions:
            regions[region_key] = dict(region_config)
            changed = True
            continue
        for field, value in region_config.items():
            if field not in regions[region_key]:
                regions[region_key][field] = value
                changed = True

    for key, value in DEFAULT_APP_SETTINGS.items():
        if key not in settings:
            settings[key] = value
            changed = True

    if changed:
        data["regions"] = regions
        data["settings"] = settings
        _save_runner_config(data)

    _ensure_default_template_dirs(regions)


def _load_runner_config():
    if not RUNNER_CONFIG_FILE.exists():
        _ensure_runner_config()
    with RUNNER_CONFIG_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("regions", {})
    data.setdefault("settings", {})
    return data


def _save_runner_config(payload):
    with RUNNER_CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _resolve_path(path_text):
    raw = (path_text or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def _parse_mssql_uri(uri):
    if not uri:
        raise ValueError("连接串为空。")

    parsed = urlparse(uri)
    if not parsed.scheme.startswith("mssql"):
        raise ValueError("仅支持 mssql+pymssql 格式连接串。")

    if not parsed.hostname:
        raise ValueError("连接串缺少主机名。")

    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError("连接串缺少数据库名。")

    return {
        "driver": os.getenv("SQLSERVER_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
        "host": parsed.hostname,
        "port": parsed.port or 1433,
        "database": database,
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }


def _choose_driver(expected_driver):
    if pyodbc is None:
        return expected_driver

    installed = []
    try:
        installed = pyodbc.drivers()
    except Exception:
        installed = []

    if expected_driver in installed:
        return expected_driver

    preferred = [d for d in installed if "ODBC Driver" in d and "SQL Server" in d]
    if preferred:
        preferred.sort(reverse=True)
        return preferred[0]
    return expected_driver


def _odbc_conn_str_from_uri(uri):
    parsed = _parse_mssql_uri(uri)
    driver = _choose_driver(parsed["driver"])
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={parsed['host']},{parsed['port']};"
        f"DATABASE={parsed['database']};"
        f"UID={parsed['username']};"
        f"PWD={parsed['password']};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )


def _list_txt_templates(template_dir):
    path = _resolve_path(template_dir)
    if path is None:
        raise ValueError("SQL 模板目录为空。")
    if not path.exists():
        raise FileNotFoundError(f"模板目录不存在: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"模板目录不是文件夹: {path}")
    files = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".txt"]
    files.sort(key=lambda p: p.name.lower())
    return files


def _read_sql_file(path):
    last_err = None
    for encoding in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return path.read_text(encoding=encoding).strip()
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"读取 SQL 文件失败: {path} ({last_err})")


def _load_sql_templates(template_dir):
    templates = []
    for file_path in _list_txt_templates(template_dir):
        sql_text = _read_sql_file(file_path)
        if not sql_text:
            continue
        templates.append({"name": file_path.name, "sql": sql_text})
    return templates


def _csv_output_path(output_dir, region_key, sql_file_name, batch_label):
    output_root = _resolve_path(output_dir)
    if output_root is None:
        raise ValueError("输出目录为空。")
    safe_name = Path(sql_file_name).stem.replace(" ", "_")
    target = output_root / batch_label / f"{region_key}_{safe_name}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if columns:
            writer.writerow(columns)
        for row in rows:
            writer.writerow(list(row))


def _run_single_template(cursor, template):
    cursor.execute(template["sql"])
    if not cursor.description:
        return {
            "columns": [],
            "rows": [],
            "row_count": max(cursor.rowcount, 0),
            "has_result_set": False,
        }
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "has_result_set": True,
    }


def _pyodbc_drivers():
    if pyodbc is None:
        return []
    try:
        return pyodbc.drivers()
    except Exception:
        return []


def _region_label(region_key, config):
    return config.get("label") or region_key


def _test_connection(uri):
    if pyodbc is None:
        return {
            "status": "error",
            "message": "pyodbc 不可用，请先安装 ODBC 运行环境。",
            "drivers": [],
        }
    conn_str = _odbc_conn_str_from_uri(uri)
    conn = None
    cursor = None
    try:
        conn = pyodbc.connect(conn_str, timeout=15)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 AS ok")
        probe = cursor.fetchone()
        value = probe[0] if probe else 1
        return {
            "status": "success",
            "message": "连接成功。",
            "probe": value,
            "drivers": _pyodbc_drivers(),
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
            "drivers": _pyodbc_drivers(),
        }
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _execute_region(region_key, region_config):
    label = _region_label(region_key, region_config)
    logs = [f"[{region_key}] 开始执行：{label}"]
    started_at = _utc_iso()
    templates = _load_sql_templates(region_config.get("template_dir"))
    if not templates:
        return {
            "region": region_key,
            "label": label,
            "status": "warning",
            "startedAt": started_at,
            "finishedAt": _utc_iso(),
            "templateCount": 0,
            "successCount": 0,
            "failCount": 0,
            "outputs": [],
            "logs": logs + [f"[{region_key}] 未找到可执行的 txt SQL 模板。"],
        }

    if pyodbc is None:
        raise RuntimeError("pyodbc 不可用，请先安装 ODBC 运行环境。")

    batch_label = datetime.now().strftime("%Y%m%d_%H%M%S")
    conn_str = _odbc_conn_str_from_uri(region_config.get("connection_uri", ""))
    conn = None
    cursor = None
    outputs = []
    success_count = 0
    fail_count = 0
    try:
        conn = pyodbc.connect(conn_str, timeout=30)
        cursor = conn.cursor()

        for template in templates:
            tpl_name = template["name"]
            logs.append(f"[{region_key}] 执行模板: {tpl_name}")
            try:
                result = _run_single_template(cursor, template)
                if result["has_result_set"]:
                    csv_path = _csv_output_path(
                        region_config.get("output_dir"),
                        region_key,
                        tpl_name,
                        batch_label,
                    )
                    _write_csv(csv_path, result["columns"], result["rows"])
                    outputs.append(
                        {
                            "template": tpl_name,
                            "rows": result["row_count"],
                            "file": str(csv_path),
                            "status": "success",
                        }
                    )
                    logs.append(f"[{region_key}] 成功: {tpl_name} -> {csv_path} ({result['row_count']} 行)")
                else:
                    conn.commit()
                    outputs.append(
                        {
                            "template": tpl_name,
                            "rows": result["row_count"],
                            "file": None,
                            "status": "success",
                            "note": "SQL 无结果集，已执行提交。",
                        }
                    )
                    logs.append(f"[{region_key}] 成功: {tpl_name} (无结果集，影响 {result['row_count']} 行)")
                success_count += 1
            except Exception as exc:
                fail_count += 1
                outputs.append(
                    {
                        "template": tpl_name,
                        "rows": 0,
                        "file": None,
                        "status": "error",
                        "error": str(exc),
                    }
                )
                logs.append(f"[{region_key}] 失败: {tpl_name} -> {exc}")

        status = "success" if fail_count == 0 else ("partial" if success_count > 0 else "error")
        return {
            "region": region_key,
            "label": label,
            "status": status,
            "startedAt": started_at,
            "finishedAt": _utc_iso(),
            "templateCount": len(templates),
            "successCount": success_count,
            "failCount": fail_count,
            "outputs": outputs,
            "logs": logs,
        }
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _normalize_region_payload(payload):
    data = payload if isinstance(payload, dict) else {}
    regions_payload = data.get("regions", {})
    settings_payload = data.get("settings", {})
    base = _load_runner_config()

    regions = dict(base.get("regions", {}))
    for key in DEFAULT_REGION_CONFIG.keys():
        incoming = regions_payload.get(key, {})
        current = regions.get(key, {})
        regions[key] = {
            "label": str(incoming.get("label", current.get("label", DEFAULT_REGION_CONFIG[key]["label"])) or ""),
            "connection_uri": str(
                incoming.get("connection_uri", current.get("connection_uri", DEFAULT_REGION_CONFIG[key]["connection_uri"]))
                or ""
            ).strip(),
            "template_dir": str(
                incoming.get("template_dir", current.get("template_dir", DEFAULT_REGION_CONFIG[key]["template_dir"])) or ""
            ).strip(),
            "output_dir": str(
                incoming.get("output_dir", current.get("output_dir", DEFAULT_REGION_CONFIG[key]["output_dir"])) or ""
            ).strip(),
        }

    settings = dict(base.get("settings", {}))
    freq_value = settings_payload.get("frequency_value", settings.get("frequency_value", DEFAULT_APP_SETTINGS["frequency_value"]))
    freq_unit = settings_payload.get("frequency_unit", settings.get("frequency_unit", DEFAULT_APP_SETTINGS["frequency_unit"]))
    try:
        freq_value = max(1, int(freq_value))
    except Exception:
        freq_value = DEFAULT_APP_SETTINGS["frequency_value"]
    if freq_unit not in ("minute", "hour"):
        freq_unit = DEFAULT_APP_SETTINGS["frequency_unit"]

    settings["frequency_value"] = freq_value
    settings["frequency_unit"] = freq_unit
    return {"regions": regions, "settings": settings}


def _next_run_hint(settings):
    now = datetime.utcnow()
    value = int(settings.get("frequency_value", 30))
    unit = settings.get("frequency_unit", "minute")
    delta = timedelta(hours=value) if unit == "hour" else timedelta(minutes=value)
    return (now + delta).isoformat() + "Z"


@app.route("/")
def index():
    _ensure_runner_config()
    return send_file(ROOT_DIR / "index.html")


@app.route("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "time": _utc_iso(),
            "configFile": str(RUNNER_CONFIG_FILE),
            "pyodbcInstalled": pyodbc is not None,
            "drivers": _pyodbc_drivers(),
        }
    )


@app.route("/api/runner-config", methods=["GET", "POST"])
def runner_config():
    _ensure_runner_config()
    if request.method == "GET":
        data = _load_runner_config()
        data["meta"] = {
            "configFile": str(RUNNER_CONFIG_FILE),
            "drivers": _pyodbc_drivers(),
            "nextRunHint": _next_run_hint(data.get("settings", {})),
        }
        return jsonify({"status": "success", "data": data})

    payload = request.get_json(silent=True) or {}
    normalized = _normalize_region_payload(payload)
    _save_runner_config(normalized)
    return jsonify({"status": "success", "message": "配置已保存。", "data": normalized})


@app.route("/api/runner/templates")
def runner_templates():
    _ensure_runner_config()
    region = (request.args.get("region") or "").strip().upper()
    config = _load_runner_config()
    region_cfg = config.get("regions", {}).get(region)
    if not region_cfg:
        return jsonify({"status": "error", "message": f"未知地区: {region}"}), 400
    try:
        files = _list_txt_templates(region_cfg.get("template_dir"))
        return jsonify(
            {
                "status": "success",
                "region": region,
                "templateDir": str(_resolve_path(region_cfg.get("template_dir"))),
                "count": len(files),
                "files": [f.name for f in files],
            }
        )
    except Exception as exc:
        return jsonify({"status": "error", "region": region, "message": str(exc)}), 400


@app.route("/api/runner/test-connection", methods=["POST"])
def runner_test_connection():
    _ensure_runner_config()
    payload = request.get_json(silent=True) or {}
    region = str(payload.get("region", "")).strip().upper()
    config = _load_runner_config()
    region_cfg = config.get("regions", {}).get(region)
    if not region_cfg:
        return jsonify({"status": "error", "message": f"未知地区: {region}"}), 400

    result = _test_connection(region_cfg.get("connection_uri"))
    result["region"] = region
    return jsonify(result), (200 if result.get("status") == "success" else 500)


@app.route("/api/runner/run", methods=["POST"])
def runner_run():
    _ensure_runner_config()
    payload = request.get_json(silent=True) or {}
    requested_regions = payload.get("regions", [])
    if not isinstance(requested_regions, list):
        return jsonify({"status": "error", "message": "regions 必须是数组。"}), 400

    requested_regions = [str(item).strip().upper() for item in requested_regions if str(item).strip()]
    if not requested_regions:
        return jsonify({"status": "error", "message": "至少选择一个地区。"}), 400

    config = _load_runner_config()
    all_regions = config.get("regions", {})
    results = []
    summary = {"regions": len(requested_regions), "success": 0, "partial": 0, "warning": 0, "error": 0}

    for region in requested_regions:
        region_cfg = all_regions.get(region)
        if not region_cfg:
            result = {
                "region": region,
                "label": region,
                "status": "error",
                "startedAt": _utc_iso(),
                "finishedAt": _utc_iso(),
                "templateCount": 0,
                "successCount": 0,
                "failCount": 0,
                "outputs": [],
                "logs": [f"[{region}] 未找到地区配置。"],
            }
        else:
            try:
                result = _execute_region(region, region_cfg)
            except Exception as exc:
                result = {
                    "region": region,
                    "label": _region_label(region, region_cfg),
                    "status": "error",
                    "startedAt": _utc_iso(),
                    "finishedAt": _utc_iso(),
                    "templateCount": 0,
                    "successCount": 0,
                    "failCount": 0,
                    "outputs": [],
                    "logs": [f"[{region}] 执行失败: {exc}"],
                }
        results.append(result)
        summary[result["status"]] = summary.get(result["status"], 0) + 1

    status = "success"
    if summary["error"] > 0 and summary["success"] == 0 and summary["partial"] == 0 and summary["warning"] == 0:
        status = "error"
    elif summary["error"] > 0 or summary["partial"] > 0:
        status = "partial"

    return jsonify(
        {
            "status": status,
            "executedAt": _utc_iso(),
            "summary": summary,
            "results": results,
        }
    )


if __name__ == "__main__":
    _ensure_runner_config()
    app.run(host="0.0.0.0", port=5000, debug=True)
