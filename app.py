import argparse
import csv
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    import pyodbc
except ImportError:
    pyodbc = None

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except Exception:
    tk = None
    ttk = None
    filedialog = None
    messagebox = None
    scrolledtext = None


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
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_path(path_text):
    raw = (path_text or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


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
            (template_dir / "example_query.txt").write_text(sample_sql, encoding="utf-8")


def _load_runner_config():
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


def _ensure_runner_config():
    if not RUNNER_CONFIG_FILE.exists():
        payload = {"regions": DEFAULT_REGION_CONFIG, "settings": DEFAULT_APP_SETTINGS}
        _save_runner_config(payload)
        _ensure_default_template_dirs(payload["regions"])
        return

    data = _load_runner_config()
    changed = False
    regions = data.get("regions", {})
    settings = data.get("settings", {})

    for region_key, region_cfg in DEFAULT_REGION_CONFIG.items():
        if region_key not in regions:
            regions[region_key] = dict(region_cfg)
            changed = True
            continue
        for field, value in region_cfg.items():
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


def _region_label(region_key, cfg):
    return cfg.get("label") or region_key


def _parse_mssql_uri(uri):
    if not uri:
        raise ValueError("连接串为空。")
    parsed = urlparse(uri)
    if not parsed.scheme.startswith("mssql"):
        raise ValueError("仅支持 mssql+pymssql:// 格式连接串。")
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
        raise ValueError("TXT 模板目录为空。")
    if not path.exists():
        raise FileNotFoundError(f"模板目录不存在: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"模板目录不是文件夹: {path}")
    files = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in (".txt", ".sql")]
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
        if sql_text:
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


def _run_single_template(cursor, sql):
    cursor.execute(sql)
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


def _drivers():
    if pyodbc is None:
        return []
    try:
        return pyodbc.drivers()
    except Exception:
        return []


def test_connection(uri):
    if pyodbc is None:
        return {
            "status": "error",
            "message": "pyodbc 不可用，请先安装 pyodbc 和 ODBC Driver。",
            "drivers": [],
        }
    conn = None
    cursor = None
    try:
        conn = pyodbc.connect(_odbc_conn_str_from_uri(uri), timeout=15)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 AS ok")
        row = cursor.fetchone()
        return {
            "status": "success",
            "message": "连接成功。",
            "probe": row[0] if row else 1,
            "drivers": _drivers(),
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
            "drivers": _drivers(),
        }
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def execute_region(region_key, region_cfg, log=None):
    def _log(text):
        if callable(log):
            log(text)

    label = _region_label(region_key, region_cfg)
    started_at = _utc_iso()
    _log(f"[{region_key}] 开始执行：{label}")
    templates = _load_sql_templates(region_cfg.get("template_dir"))
    if not templates:
        _log(f"[{region_key}] 未找到可执行的 txt 模板。")
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
        }

    if pyodbc is None:
        raise RuntimeError("pyodbc 不可用，请先安装 pyodbc 和 ODBC Driver。")

    batch_label = datetime.now().strftime("%Y%m%d_%H%M%S")
    conn = None
    cursor = None
    outputs = []
    success_count = 0
    fail_count = 0

    try:
        conn = pyodbc.connect(_odbc_conn_str_from_uri(region_cfg.get("connection_uri", "")), timeout=30)
        cursor = conn.cursor()
        for tpl in templates:
            tpl_name = tpl["name"]
            _log(f"[{region_key}] 执行模板：{tpl_name}")
            try:
                result = _run_single_template(cursor, tpl["sql"])
                if result["has_result_set"]:
                    output_path = _csv_output_path(region_cfg.get("output_dir"), region_key, tpl_name, batch_label)
                    _write_csv(output_path, result["columns"], result["rows"])
                    outputs.append(
                        {
                            "template": tpl_name,
                            "rows": result["row_count"],
                            "file": str(output_path),
                            "status": "success",
                        }
                    )
                    _log(f"[{region_key}] 成功：{tpl_name} -> {output_path} ({result['row_count']} 行)")
                else:
                    conn.commit()
                    outputs.append(
                        {
                            "template": tpl_name,
                            "rows": result["row_count"],
                            "file": None,
                            "status": "success",
                            "note": "无结果集，已执行提交。",
                        }
                    )
                    _log(f"[{region_key}] 成功：{tpl_name} (无结果集，影响 {result['row_count']} 行)")
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
                _log(f"[{region_key}] 失败：{tpl_name} -> {exc}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

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
    }


def run_regions(selected_regions, config, log=None):
    regions = config.get("regions", {})
    results = []
    summary = {"regions": len(selected_regions), "success": 0, "partial": 0, "warning": 0, "error": 0}

    for region in selected_regions:
        region_cfg = regions.get(region)
        if not region_cfg:
            if callable(log):
                log(f"[{region}] 未找到地区配置。")
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
            }
        else:
            try:
                result = execute_region(region, region_cfg, log=log)
            except Exception as exc:
                if callable(log):
                    log(f"[{region}] 执行失败：{exc}")
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
                }
        results.append(result)
        summary[result["status"]] = summary.get(result["status"], 0) + 1

    final_status = "success"
    if summary["error"] > 0 and summary["success"] == 0 and summary["partial"] == 0 and summary["warning"] == 0:
        final_status = "error"
    elif summary["error"] > 0 or summary["partial"] > 0:
        final_status = "partial"

    return {
        "status": final_status,
        "executedAt": _utc_iso(),
        "summary": summary,
        "results": results,
    }


class DesktopRunnerApp:
    def __init__(self):
        if tk is None:
            raise RuntimeError("当前 Python 环境不可用 Tkinter，无法启动桌面界面。")
        _ensure_runner_config()
        self.config_data = _load_runner_config()
        self.region_order = ["NZ", "AU", "CA"]

        self.root = tk.Tk()
        self.root.title("多地区 SQL 执行器（纯 Python）")
        self.root.geometry("1180x860")
        self.root.minsize(1000, 720)

        self.busy = False
        self.schedule_enabled = False
        self.schedule_job = None

        self.region_vars = {k: tk.BooleanVar(value=(k == "NZ")) for k in self.region_order}
        self.edit_region_var = tk.StringVar(value=self.region_order[0])
        settings = self.config_data.get("settings", {})
        self.freq_value_var = tk.IntVar(value=int(settings.get("frequency_value", 30)))
        self.freq_unit_var = tk.StringVar(value=settings.get("frequency_unit", "minute"))
        self.status_var = tk.StringVar(value="就绪")
        self.next_run_var = tk.StringVar(value="未调度")
        self.progress_var = tk.DoubleVar(value=0)
        self.save_hint_var = tk.StringVar(value="已加载")

        self._build_ui()
        self._load_edit_form(self.edit_region_var.get())
        self.log("程序已启动。")

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(top, text="多地区 SQL 执行器（纯 Python 桌面版）", font=("Segoe UI", 14, "bold"))
        title.pack(anchor=tk.W)
        ttk.Label(top, text="不走浏览器；连接串、TXT SQL 模板、执行日志全部在本地窗口。").pack(anchor=tk.W, pady=(2, 10))

        region_frame = ttk.LabelFrame(top, text="这次要跑的地区（可多选，一次执行）", padding=10)
        region_frame.pack(fill=tk.X, pady=6)
        for key in self.region_order:
            cfg = self.config_data["regions"].get(key, {})
            text = f"{cfg.get('label', key)} ({key})"
            ttk.Checkbutton(region_frame, text=text, variable=self.region_vars[key]).pack(side=tk.LEFT, padx=(0, 16))

        config_frame = ttk.LabelFrame(top, text="地区配置（连接串 / TXT目录 / 输出目录）", padding=10)
        config_frame.pack(fill=tk.X, pady=6)

        row0 = ttk.Frame(config_frame)
        row0.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row0, text="正在编辑").pack(side=tk.LEFT)
        self.edit_region_combo = ttk.Combobox(
            row0,
            width=18,
            state="readonly",
            values=[f"{k} {self.config_data['regions'].get(k, {}).get('label', '')}".strip() for k in self.region_order],
        )
        self.edit_region_combo.current(0)
        self.edit_region_combo.pack(side=tk.LEFT, padx=8)
        self.edit_region_combo.bind("<<ComboboxSelected>>", self._on_edit_region_change)
        ttk.Label(row0, textvariable=self.save_hint_var).pack(side=tk.LEFT, padx=8)

        form = ttk.Frame(config_frame)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="数据库连接").grid(row=0, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        self.conn_text = tk.Text(form, height=3, wrap=tk.WORD)
        self.conn_text.grid(row=0, column=1, sticky=tk.EW, pady=4)
        self.conn_text.bind("<KeyRelease>", lambda _e: self._mark_unsaved())
        self.test_btn = ttk.Button(form, text="测试连接", command=self.test_connection_action)
        self.test_btn.grid(row=0, column=2, sticky=tk.E, padx=(8, 0), pady=4)

        ttk.Label(form, text="TXT 模板目录").grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        self.template_dir_entry = ttk.Entry(form)
        self.template_dir_entry.grid(row=1, column=1, sticky=tk.EW, pady=4)
        self.template_dir_entry.bind("<KeyRelease>", lambda _e: self._mark_unsaved())
        scan_box = ttk.Frame(form)
        scan_box.grid(row=1, column=2, sticky=tk.E, padx=(8, 0), pady=4)
        self.scan_btn = ttk.Button(scan_box, text="读取TXT", command=self.scan_templates_action)
        self.scan_btn.pack(side=tk.LEFT)
        ttk.Button(scan_box, text="浏览", command=self.pick_template_dir).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(form, text="输出目录").grid(row=2, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        self.output_dir_entry = ttk.Entry(form)
        self.output_dir_entry.grid(row=2, column=1, sticky=tk.EW, pady=4)
        self.output_dir_entry.bind("<KeyRelease>", lambda _e: self._mark_unsaved())
        out_box = ttk.Frame(form)
        out_box.grid(row=2, column=2, sticky=tk.E, padx=(8, 0), pady=4)
        ttk.Button(out_box, text="浏览", command=self.pick_output_dir).pack(side=tk.LEFT)
        ttk.Button(out_box, text="提示路径", command=self.show_output_hint).pack(side=tk.LEFT, padx=(6, 0))

        self.template_list = tk.Listbox(config_frame, height=5)
        self.template_list.pack(fill=tk.X, pady=(8, 0))
        self.template_list.insert(tk.END, "尚未读取模板目录。")

        schedule_frame = ttk.LabelFrame(top, text="调度设置", padding=10)
        schedule_frame.pack(fill=tk.X, pady=6)
        ttk.Label(schedule_frame, text="执行频率").pack(side=tk.LEFT)
        self.freq_spin = ttk.Spinbox(schedule_frame, from_=1, to=1440, width=8, textvariable=self.freq_value_var)
        self.freq_spin.pack(side=tk.LEFT, padx=6)
        self.freq_combo = ttk.Combobox(schedule_frame, width=10, state="readonly", values=["minute", "hour"], textvariable=self.freq_unit_var)
        self.freq_combo.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(schedule_frame, text="下次执行").pack(side=tk.LEFT)
        ttk.Label(schedule_frame, textvariable=self.next_run_var).pack(side=tk.LEFT, padx=(6, 0))

        action_frame = ttk.Frame(top)
        action_frame.pack(fill=tk.X, pady=(4, 6))
        self.run_btn = ttk.Button(action_frame, text="立即执行一次", command=lambda: self.run_action(from_schedule=False))
        self.run_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.schedule_start_btn = ttk.Button(action_frame, text="开始自动调度", command=self.start_schedule_action)
        self.schedule_start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.schedule_stop_btn = ttk.Button(action_frame, text="停止自动调度", command=self.stop_schedule_action)
        self.schedule_stop_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.save_btn = ttk.Button(action_frame, text="保存配置", command=self.save_config_action)
        self.save_btn.pack(side=tk.LEFT)

        status_frame = ttk.Frame(top)
        status_frame.pack(fill=tk.X, pady=6)
        ttk.Label(status_frame, text="状态：").pack(side=tk.LEFT)
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var)
        self.status_label.pack(side=tk.LEFT)
        self.summary_label = ttk.Label(status_frame, text="")
        self.summary_label.pack(side=tk.RIGHT)

        self.progress = ttk.Progressbar(top, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 8))

        log_frame = ttk.LabelFrame(top, text="执行日志", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 4))
        self.log_box = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=18)
        self.log_box.pack(fill=tk.BOTH, expand=True)
        self.log_box.configure(state=tk.DISABLED)

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    def on_close(self):
        self.schedule_enabled = False
        if self.schedule_job:
            self.root.after_cancel(self.schedule_job)
            self.schedule_job = None
        self.root.destroy()

    def log(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.insert(tk.END, f"[{ts}] {text}\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state=tk.DISABLED)

    def _ui_log_from_thread(self, text):
        self.root.after(0, lambda: self.log(text))

    def _set_status(self, text):
        self.status_var.set(text)

    def _set_busy(self, busy):
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for widget in (self.test_btn, self.scan_btn, self.save_btn, self.run_btn, self.schedule_start_btn, self.freq_spin, self.freq_combo):
            widget.configure(state=state)
        self.schedule_stop_btn.configure(state=tk.NORMAL)
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(9)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress_var.set(0)

    def _mark_unsaved(self):
        self.save_hint_var.set("有未保存改动")

    def _current_edit_region(self):
        text = self.edit_region_combo.get().strip()
        return text.split(" ")[0] if text else self.region_order[0]

    def _sync_edit_form_to_config(self):
        region = self._current_edit_region()
        cfg = self.config_data["regions"][region]
        cfg["connection_uri"] = self.conn_text.get("1.0", tk.END).strip()
        cfg["template_dir"] = self.template_dir_entry.get().strip()
        cfg["output_dir"] = self.output_dir_entry.get().strip()

    def _load_edit_form(self, region):
        cfg = self.config_data["regions"][region]
        self.conn_text.delete("1.0", tk.END)
        self.conn_text.insert("1.0", cfg.get("connection_uri", ""))
        self.template_dir_entry.delete(0, tk.END)
        self.template_dir_entry.insert(0, cfg.get("template_dir", ""))
        self.output_dir_entry.delete(0, tk.END)
        self.output_dir_entry.insert(0, cfg.get("output_dir", ""))
        self.template_list.delete(0, tk.END)
        self.template_list.insert(tk.END, "尚未读取模板目录。")
        self.save_hint_var.set("已加载")

    def _on_edit_region_change(self, _event=None):
        self._sync_edit_form_to_config()
        self._load_edit_form(self._current_edit_region())

    def pick_template_dir(self):
        if filedialog is None:
            return
        current = self.template_dir_entry.get().strip()
        start_dir = str(_resolve_path(current) or ROOT_DIR)
        selected = filedialog.askdirectory(initialdir=start_dir)
        if selected:
            self.template_dir_entry.delete(0, tk.END)
            self.template_dir_entry.insert(0, selected)
            self._mark_unsaved()

    def pick_output_dir(self):
        if filedialog is None:
            return
        current = self.output_dir_entry.get().strip()
        start_dir = str(_resolve_path(current) or ROOT_DIR)
        selected = filedialog.askdirectory(initialdir=start_dir)
        if selected:
            self.output_dir_entry.delete(0, tk.END)
            self.output_dir_entry.insert(0, selected)
            self._mark_unsaved()

    def show_output_hint(self):
        self._sync_edit_form_to_config()
        region = self._current_edit_region()
        cfg = self.config_data["regions"][region]
        self.log(f"[{region}] 当前输出目录：{cfg.get('output_dir', '-')}")

    def selected_regions(self):
        regions = [k for k, var in self.region_vars.items() if var.get()]
        return regions or [self.region_order[0]]

    def _run_in_background(self, status_text, worker_fn, done_fn):
        if self.busy:
            self.log("已有任务在执行，请稍候。")
            return

        self._set_busy(True)
        self._set_status(status_text)

        def _worker():
            try:
                result = worker_fn()
                self.root.after(0, lambda: done_fn(None, result))
            except Exception as exc:
                self.root.after(0, lambda: done_fn(exc, None))

        threading.Thread(target=_worker, daemon=True).start()

    def scan_templates_action(self):
        self._sync_edit_form_to_config()
        region = self._current_edit_region()
        cfg = self.config_data["regions"][region]
        try:
            files = _list_txt_templates(cfg.get("template_dir"))
            self.template_list.delete(0, tk.END)
            if not files:
                self.template_list.insert(tk.END, "目录下没有 .txt 模板。")
            else:
                for f in files:
                    self.template_list.insert(tk.END, f.name)
            self.log(f"[{region}] 找到 {len(files)} 个 txt 模板。")
            self._set_status("模板读取完成")
        except Exception as exc:
            self.template_list.delete(0, tk.END)
            self.template_list.insert(tk.END, f"读取失败：{exc}")
            self.log(f"[{region}] 模板读取失败：{exc}")
            self._set_status("模板读取失败")

    def save_config_action(self):
        self._sync_edit_form_to_config()
        settings = self.config_data.setdefault("settings", {})
        try:
            settings["frequency_value"] = max(1, int(self.freq_value_var.get()))
        except Exception:
            settings["frequency_value"] = DEFAULT_APP_SETTINGS["frequency_value"]
            self.freq_value_var.set(settings["frequency_value"])
        settings["frequency_unit"] = self.freq_unit_var.get() if self.freq_unit_var.get() in ("minute", "hour") else "minute"
        self.freq_unit_var.set(settings["frequency_unit"])
        _save_runner_config(self.config_data)
        self.save_hint_var.set("已保存")
        self.log("配置保存成功。")
        self._set_status("配置已保存")

    def test_connection_action(self):
        self._sync_edit_form_to_config()
        region = self._current_edit_region()
        cfg = self.config_data["regions"][region]

        def worker():
            return test_connection(cfg.get("connection_uri"))

        def done(err, result):
            self._set_busy(False)
            if err:
                self.log(f"[{region}] 连接测试失败：{err}")
                self._set_status("连接测试失败")
                return
            if result.get("status") == "success":
                self.log(f"[{region}] 连接成功。驱动：{', '.join(result.get('drivers', [])) or '-'}")
                self._set_status("连接测试成功")
            else:
                self.log(f"[{region}] 连接失败：{result.get('message')}")
                self._set_status("连接测试失败")

        self._run_in_background("连接测试中...", worker, done)

    def run_action(self, from_schedule=False):
        self._sync_edit_form_to_config()
        selected = self.selected_regions()
        if not selected:
            self.log("请至少选择一个地区。")
            self._set_status("未选择地区")
            return

        self.log(f"开始执行地区：{', '.join(selected)}")

        def worker():
            return run_regions(selected, self.config_data, log=self._ui_log_from_thread)

        def done(err, result):
            self._set_busy(False)
            if err:
                self.log(f"执行失败：{err}")
                self._set_status("执行失败")
                return
            summary = result.get("summary", {})
            self.summary_label.configure(
                text=f"地区:{summary.get('regions', 0)} 成功:{summary.get('success', 0)} 部分:{summary.get('partial', 0)} 警告:{summary.get('warning', 0)} 失败:{summary.get('error', 0)}"
            )
            status = result.get("status")
            if status == "success":
                self._set_status("执行完成")
            elif status == "partial":
                self._set_status("部分成功")
            else:
                self._set_status("执行失败")
            if from_schedule and self.schedule_enabled:
                self.log("自动调度执行完成。")

        self._run_in_background("执行中...", worker, done)

    def _interval_ms(self):
        value = max(1, int(self.freq_value_var.get() or 30))
        unit = self.freq_unit_var.get()
        return value * 3600 * 1000 if unit == "hour" else value * 60 * 1000

    def _next_run_text(self):
        next_time = datetime.now() + timedelta(milliseconds=self._interval_ms())
        return next_time.strftime("%Y-%m-%d %H:%M:%S")

    def _schedule_tick(self):
        self.schedule_job = None
        if not self.schedule_enabled:
            return
        self.next_run_var.set(self._next_run_text())
        if not self.busy:
            self.run_action(from_schedule=True)
        else:
            self.log("自动调度触发时任务仍在运行，已跳过本轮。")
        self.schedule_job = self.root.after(self._interval_ms(), self._schedule_tick)

    def start_schedule_action(self):
        self.save_config_action()
        if self.schedule_job:
            self.root.after_cancel(self.schedule_job)
            self.schedule_job = None
        self.schedule_enabled = True
        self.next_run_var.set(self._next_run_text())
        self.schedule_job = self.root.after(self._interval_ms(), self._schedule_tick)
        self.log("自动调度已启动。")
        self._set_status("自动调度中")

    def stop_schedule_action(self):
        self.schedule_enabled = False
        if self.schedule_job:
            self.root.after_cancel(self.schedule_job)
            self.schedule_job = None
        self.next_run_var.set("未调度")
        self.log("自动调度已停止。")
        self._set_status("已停止自动调度")


def run_cli_once(region_arg):
    _ensure_runner_config()
    config = _load_runner_config()
    if region_arg:
        selected = [r.strip().upper() for r in region_arg.split(",") if r.strip()]
    else:
        selected = list(DEFAULT_REGION_CONFIG.keys())
    result = run_regions(selected, config, log=print)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in ("success", "partial") else 1


def main():
    parser = argparse.ArgumentParser(description="多地区 SQL 执行器（纯 Python）")
    parser.add_argument("--run-once", action="store_true", help="命令行执行一次，不启动桌面界面")
    parser.add_argument("--regions", default="", help="命令行执行地区，例如 NZ,AU,CA")
    args = parser.parse_args()

    if args.run_once:
        raise SystemExit(run_cli_once(args.regions))

    if tk is None:
        print("当前 Python 缺少 Tkinter，无法启动桌面界面。")
        print("可改用命令行执行：python app.py --run-once --regions NZ,AU,CA")
        raise SystemExit(1)

    app = DesktopRunnerApp()
    app.run()


if __name__ == "__main__":
    main()
