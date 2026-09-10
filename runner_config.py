"""多地区 SQL 执行器 / 看板共用的地区配置加载。

用户点「保存配置」写入 region_runner_config.local.json（已 gitignore），
避免 git pull 把 connection_uri 等本地改动覆盖掉。
"""

import json
from copy import deepcopy
from pathlib import Path

ROOT_DIR = Path(__file__).parent
RUNNER_CONFIG_FILE = ROOT_DIR / "region_runner_config.json"
RUNNER_CONFIG_LOCAL_FILE = ROOT_DIR / "region_runner_config.local.json"

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


def _read_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _write_json(path, payload):
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _merge_dict(base, overlay):
    if not overlay:
        return base
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def _merge_regions(base_regions, overlay_regions):
    out = {str(k).strip().upper(): dict(v) for k, v in (base_regions or {}).items() if isinstance(v, dict)}
    for key, cfg in (overlay_regions or {}).items():
        if not isinstance(cfg, dict):
            continue
        rk = str(key).strip().upper()
        merged = dict(out.get(rk, {}))
        merged.update(cfg)
        out[rk] = merged
    return out


def load_runner_config():
    """默认配置 + region_runner_config.json + region_runner_config.local.json（后者优先）。"""
    regions = deepcopy(DEFAULT_REGION_CONFIG)
    settings = dict(DEFAULT_APP_SETTINGS)

    if RUNNER_CONFIG_FILE.exists():
        file_data = _read_json(RUNNER_CONFIG_FILE)
        regions = _merge_regions(regions, file_data.get("regions"))
        settings = _merge_dict(settings, file_data.get("settings", {}))

    if RUNNER_CONFIG_LOCAL_FILE.exists():
        local_data = _read_json(RUNNER_CONFIG_LOCAL_FILE)
        regions = _merge_regions(regions, local_data.get("regions"))
        settings = _merge_dict(settings, local_data.get("settings", {}))

    return {"regions": regions, "settings": settings}


def save_runner_config(payload):
    """保存到本地覆盖文件，不被 git 跟踪。"""
    _write_json(RUNNER_CONFIG_LOCAL_FILE, payload)


def ensure_runner_config():
    """确保仓库内默认配置文件存在，并补齐缺失字段（不覆盖用户 local 配置）。"""
    if not RUNNER_CONFIG_FILE.exists():
        _write_json(
            RUNNER_CONFIG_FILE,
            {"regions": deepcopy(DEFAULT_REGION_CONFIG), "settings": dict(DEFAULT_APP_SETTINGS)},
        )
        return

    data = _read_json(RUNNER_CONFIG_FILE)
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
        _write_json(RUNNER_CONFIG_FILE, data)
