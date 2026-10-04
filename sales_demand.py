"""渠道 sales 导出（sales 8-30 / 15 / 30）→ 日均需求，对齐供应链决策「采用需求」。

优先读列「采用需求 / AppliedDemand」；否则按窗口销量 ÷ 天数（8–30 按 23 天）。
可与智能库存决策系统 V4 导出列名兼容（模糊匹配）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import panel_data as pd

WINDOWS = (
    ("8-30", 23, ("sales 8-30", "sales_8_30", "sales8-30", "sales_8-30")),
    ("15", 15, ("sales 15", "sales_15", "sales15")),
    ("30", 30, ("sales 30", "sales_30", "sales30")),
)

APPLIED_DEMAND_KEYS = [
    "applieddemand", "applied_demand", "adopteddemand", "dailydemand",
    "采用需求", "日均需求", "预估日需求", "日需求",
]
DEMAND_SOURCE_KEYS = ["demandsource", "demand_source", "需求来源", "采用来源"]
REGION_KEYS = ["region", "地区", "island", "南北岛", "destinationregion"]
CHANNEL_KEYS = ["channel", "渠道", "sku_prefix"] + pd.CHANNEL_KEYS
SKU_KEYS = pd.CODE_KEYS
NAME_KEYS = pd.NAME_KEYS

WINDOW_QTY_KEYS = {
    "8-30": [
        "8-30", "8_30", "830", "sales_8_30", "qty_8_30", "sales830",
        "8-30天", "8_30天", "销量8-30", "sales8-30",
    ],
    "15": ["15", "15天", "sales_15", "qty_15", "销量15", "sales15"],
    "30": ["30", "30天", "sales_30", "qty_30", "销量30", "sales30"],
}
VOLUME_COEF_KEYS = [
    "体积系数", "volumecoefficient", "volume_coefficient", "coef",
    "volumewithbox", "volume_with_box", "priceradarvolume",
]
STOCKOUT_RATE_KEYS = [
    "stockoutrate", "stockout_rate", "stock_out_rate", "oos_rate", "缺货率",
]


@dataclass
class SalesDemandRecord:
    sku: str
    channel: str
    region: str
    avg_daily_units: float
    demand_source: str
    qty_windows: dict[str, float]
    unit_volume_m3: float | None
    stockout_rate_pct: float | None
    raw_name: str


def _pick(row: dict, keys: list[str]):
    return pd._pick(row, keys)


def _to_float(val, default=None):
    return pd._to_float(val) if val is not None else default


def _norm_key(sku: str, channel: str, region: str) -> tuple[str, str, str]:
    return (
        pd._norm_code(sku),
        (channel or pd.sku_prefix(sku) or "").strip().upper(),
        str(region or "").strip(),
    )


def _qty_for_window(row: dict, window: str) -> float | None:
    lower = {str(k).strip().lower(): v for k, v in row.items()}
    for key in WINDOW_QTY_KEYS.get(window, []):
        k = key.lower()
        if k in lower:
            return _to_float(lower[k], 0.0)
    # 模糊：列名含 8-30 等
    for col, val in lower.items():
        if window.replace("-", "") in col.replace("-", "").replace("_", ""):
            return _to_float(val, 0.0)
    return None


def _pick_applied_daily(row: dict, windows_state: dict[str, float]) -> tuple[float, str]:
    applied = _pick(row, APPLIED_DEMAND_KEYS)
    if applied is not None:
        val = _to_float(applied)
        if val is not None and val >= 0:
            src = str(_pick(row, DEMAND_SOURCE_KEYS) or "采用需求列").strip()
            return val, src
    # 默认与 V4 类似：优先有销量的 8-30 → 15 → 30
    for window, days, _stems in WINDOWS:
        qty = windows_state.get(window)
        if qty is not None and qty > 0:
            return qty / days, f"{window}天"
    return 0.0, ""


def _merge_row(
    acc: dict[tuple[str, str, str], dict[str, Any]],
    row: dict,
    window: str | None,
    window_days: int | None,
):
    sku = str(_pick(row, SKU_KEYS) or "").strip()
    if not sku:
        return
    channel = str(_pick(row, CHANNEL_KEYS) or pd.sku_prefix(sku) or "").strip()
    region = str(_pick(row, REGION_KEYS) or "").strip()
    key = _norm_key(sku, channel, region)
    slot = acc.setdefault(
        key,
        {
            "sku": sku,
            "channel": channel.upper() if channel else pd.sku_prefix(sku),
            "region": region,
            "name": str(_pick(row, NAME_KEYS) or "").strip(),
            "windows": {},
            "row": row,
        },
    )
    if window and window_days:
        qty = _qty_for_window(row, window)
        if qty is not None:
            slot["windows"][window] = float(qty or 0)
    else:
        for w, days, _ in WINDOWS:
            qty = _qty_for_window(row, w)
            if qty is not None:
                slot["windows"][w] = float(qty or 0)
    # 保留信息最全的一行
    if len(row) > len(slot.get("row") or {}):
        slot["row"] = row


def load_sales_demand_index(region: str | None = None) -> tuple[dict[tuple[str, str, str], SalesDemandRecord], list[str]]:
    """
    读取 Data/Output 下 sales 8-30 / 15 / 30 文本或合并表。
    返回 (index, warnings)。
    """
    region_key = str(region or pd.default_region() or "NZ").strip().upper()
    bundle = pd.get_region_bundle(region_key)
    data_dir = Path(bundle.get("data_dir") or "")
    search_dirs: list[Path] = [data_dir]
    try:
        from runner_config import load_runner_config

        reg = (load_runner_config().get("regions") or {}).get(region_key) or {}
        tpl = Path(str(reg.get("template_dir") or f"Data-{region_key}"))
        if not tpl.is_absolute():
            tpl = Path(__file__).resolve().parent / tpl
        if tpl.is_dir():
            search_dirs.append(tpl)
    except Exception:
        pass
    warnings: list[str] = []
    acc: dict[tuple[str, str, str], dict[str, Any]] = {}

    def _find(stems):
        for d in search_dirs:
            found = pd._find_region_data_file(d, stems)
            if found:
                return found
        return None

    loaded_any = False
    for window, days, stems in WINDOWS:
        path = _find(stems)
        if not path or not Path(path).is_file():
            continue
        loaded_any = True
        rows = pd._read_table(path)
        warnings.append(f"已读 {Path(path).name}（{len(rows)} 行）")
        for row in rows:
            _merge_row(acc, row, window, days)

    # 合并宽表（单文件含多窗口列）
    for stems in (("sales", "sales_merged", "sales_channel"),):
        path = _find(stems)
        if not path or not Path(path).is_file():
            continue
        if any(Path(path).name.lower().startswith(s.split()[0]) for s, _, _ in WINDOWS):
            continue
        rows = pd._read_table(path)
        if not rows:
            continue
        sample = {str(k).lower() for k in rows[0]}
        if not any(w in "".join(sample) for w in ("8-30", "15", "30", "采用")):
            continue
        loaded_any = True
        warnings.append(f"已读宽表 {Path(path).name}")
        for row in rows:
            _merge_row(acc, row, None, None)

    if not loaded_any:
        return {}, ["未找到 sales 8-30 / 15 / 30 导出（Data-NZ 下 sales *.txt）"]

    mode = os.getenv("INVENTORY_DEMAND_PICK", "v4_default").strip().lower()
    out: dict[tuple[str, str, str], SalesDemandRecord] = {}
    for key, slot in acc.items():
        row = slot.get("row") or {}
        windows = slot.get("windows") or {}
        avg, src = _pick_applied_daily(row, windows)
        if mode == "max_window":
            best = 0.0
            best_src = ""
            for window, days, _ in WINDOWS:
                q = windows.get(window, 0.0)
                daily = q / days if days else 0.0
                if daily > best:
                    best, best_src = daily, f"{window}天max"
            if best > 0:
                avg, src = best, best_src

        vol = _to_float(_pick(row, VOLUME_COEF_KEYS))
        so_raw = _pick(row, STOCKOUT_RATE_KEYS)
        so_pct = None
        if so_raw is not None:
            v = _to_float(so_raw)
            if v is not None:
                so_pct = v if v > 1 else v * 100.0

        out[key] = SalesDemandRecord(
            sku=slot["sku"],
            channel=slot["channel"],
            region=slot["region"],
            avg_daily_units=round(avg, 6),
            demand_source=src or "无销量",
            qty_windows={k: round(v, 4) for k, v in windows.items()},
            unit_volume_m3=vol,
            stockout_rate_pct=so_pct,
            raw_name=slot.get("name") or "",
        )
    return out, warnings
