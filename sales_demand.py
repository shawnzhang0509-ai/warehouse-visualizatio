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
from channel_prefixes import normalize_sku_channel_code

WINDOWS = (
    (
        "8-30",
        23,
        (
            "sales 8-30", "sales_8_30", "sales8-30", "sales_8-30",
            "Sales 8-30", "Sales_8-30", "Sales 8_30",
        ),
    ),
    ("15", 15, ("sales 15", "sales_15", "sales15", "Sales 15", "Sales_15")),
    ("30", 30, ("sales 30", "sales_30", "sales30", "Sales 30", "Sales_30")),
)

APPLIED_DEMAND_KEYS = [
    "applieddemand", "applied_demand", "adopteddemand", "dailydemand",
    "采用需求", "日均需求", "采用日均需求", "预估日需求", "日需求",
]
# 智能库存决策 V4 / SQL 导出常见列（已是「件/天」，不要再 ÷ 窗口天数）
V4_DAILY_DEMAND_KEYS = [
    "avgdailydemand_3checkins_avg",
    "avg_daily_demand_3checkins_avg",
    "singleavgdailydemand",
    "singleavgdailysales",
    "avgdailysales",
    "avgdailydemand",
    "avg_daily_sales",
    "avg_dailysales",
]
V4_WINDOW_DEMAND_KEYS = {
    "8-30": ["需求_8_30天", "需求_8-30天", "demand_8_30", "demand_8-30"],
    "15": ["需求_15天", "demand_15"],
    "30": ["需求_30天", "demand_30"],
}
SAMPLE_QTY_KEYS = ["sampleqty", "sample_qty", "样品销量", "样本销量"]
SAMPLE_DAYS_KEYS = ["sampledays", "sample_days", "样本天数", "sampleday"]
DEMAND_SOURCE_KEYS = ["demandsource", "demand_source", "需求来源", "采用来源"]
REGION_KEYS = ["region", "地区", "island", "南北岛", "destinationregion"]
CHANNEL_KEYS = ["channel", "渠道", "sku_prefix"] + pd.CHANNEL_KEYS


def normalize_demand_island(text: str | None) -> str:
    """sales / weekly 行的地区 → 北岛 | 南岛 | 空（未标注）。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    low = raw.lower().replace(" ", "")
    if raw in ("北岛", "北", "North", "NI", "North Island", "NorthIsland") or "north" in low:
        return "北岛"
    if raw in ("南岛", "南", "South", "SI", "South Island", "SouthIsland") or "south" in low:
        return "南岛"
    if "南" in raw:
        return "南岛"
    if "北" in raw:
        return "北岛"
    return ""


def filter_demand_index_by_island(
    index: dict[tuple[str, str, str], Any],
    island_scope: str,
) -> dict[tuple[str, str, str], Any]:
    """库存健康按岛：只保留 Region/南北岛 与筛选一致的 sales 行。"""
    scope = normalize_demand_island(island_scope)
    if not scope:
        return dict(index or {})
    out: dict[tuple[str, str, str], Any] = {}
    for key, rec in (index or {}).items():
        reg = normalize_demand_island(getattr(rec, "region", "") or "")
        if reg == scope:
            out[key] = rec
    return out
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
    ch = normalize_sku_channel_code(channel, sku) or pd.sku_prefix(sku)
    return (
        pd.sku_join_key(sku),
        ch,
        str(region or "").strip(),
    )


def _qty_for_window(row: dict, window: str) -> float | None:
    lower = {str(k).strip().lower(): v for k, v in row.items()}
    for key in WINDOW_QTY_KEYS.get(window, []):
        k = key.lower()
        if k in lower:
            return _to_float(lower[k], 0.0)
    return None


def _pick_cell(row: dict, keys: list[str]):
    val = _pick(row, keys)
    if val is not None:
        return val
    return pd._pick_fuzzy(row, keys)


def _precomputed_daily_units(row: dict) -> float | None:
    """已是日均件数的列（V4 aggregate_sales_demand / ensure_sales_demand_column）。"""
    for keys in (APPLIED_DEMAND_KEYS, V4_DAILY_DEMAND_KEYS):
        raw = _pick_cell(row, keys)
        if raw is None:
            continue
        val = _to_float(raw)
        if val is not None and val >= 0:
            return float(val)
    for window, keys in V4_WINDOW_DEMAND_KEYS.items():
        raw = _pick_cell(row, keys)
        if raw is None:
            continue
        val = _to_float(raw)
        if val is not None and val >= 0:
            return float(val)
    qty = _pick_cell(row, SAMPLE_QTY_KEYS)
    days = _pick_cell(row, SAMPLE_DAYS_KEYS)
    q = _to_float(qty)
    d = _to_float(days)
    if q is not None and d is not None and d > 0:
        return float(q) / float(d)
    return None


def _pick_applied_daily(row: dict, windows_state: dict[str, float]) -> tuple[float, str]:
    """与 V4.15 一致：采用需求列优先；否则 max(8-30/23, 15/15, 30/30) 的日均件数。"""
    explicit_src = str(_pick(row, DEMAND_SOURCE_KEYS) or "").strip()
    pre = _precomputed_daily_units(row)
    if pre is not None and pre > 0:
        return pre, explicit_src or "日均需求列"

    candidates: list[tuple[float, str]] = []
    if pre is not None and pre == 0:
        candidates.append((0.0, "日均需求列"))

    for window, days, _stems in WINDOWS:
        qty = windows_state.get(window)
        if qty is not None and qty > 0 and days:
            candidates.append((float(qty) / float(days), f"{window}天"))
        wpre = _pick_cell(row, V4_WINDOW_DEMAND_KEYS.get(window, []))
        if wpre is not None:
            v = _to_float(wpre)
            if v is not None and v >= 0:
                candidates.append((float(v), f"{window}天列"))

    if not candidates:
        return 0.0, explicit_src or ""

    best_val, best_src = max(candidates, key=lambda item: item[0])
    if explicit_src:
        best_src = explicit_src
    elif sum(1 for v, _ in candidates if v == best_val) > 1:
        tied = [s for v, s in candidates if v == best_val]
        best_src = "/".join(dict.fromkeys(tied))
    return best_val, best_src


def _find_sales_file_in_dir(folder: Path, stems: tuple[str, ...]) -> Path | None:
    folder = Path(folder)
    if not folder.is_dir():
        return None
    for stem in stems:
        for ext in (".csv", ".txt"):
            candidate = folder / f"{stem}{ext}"
            if candidate.is_file():
                return candidate
    stem_set = {s.lower().replace("_", " ").replace("-", " ") for s in stems}
    for candidate in sorted(folder.glob("*.csv")) + sorted(folder.glob("*.txt")):
        norm = candidate.stem.lower().replace("_", " ").replace("-", " ")
        if norm in stem_set:
            return candidate
        if "sales" in norm and any(w in norm for w in ("8 30", "830", "15", "30")):
            return candidate
    return None


def _load_per_channel_sales_dirs(
    data_dir: Path,
    acc: dict[tuple[str, str, str], dict[str, Any]],
    warnings: list[str],
) -> bool:
    """V4 渠道子目录：Output-NZ/996/Sales 8-30.csv 等。"""
    loaded = False
    if not data_dir.is_dir():
        return False
    for child in sorted(data_dir.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name in ("latest", "config", "_config") or re.fullmatch(r"\d{8}_\d{6}", name):
            continue
        ch_hint = re.match(r"^(\d{3})", name)
        if not ch_hint:
            continue
        for window, days, stems in WINDOWS:
            path = _find_sales_file_in_dir(child, stems)
            if not path:
                continue
            rows = pd._read_table(path)
            if not rows:
                continue
            loaded = True
            warnings.append(f"已读 {name}/{path.name}（{len(rows)} 行）")
            for row in rows:
                if not _pick(row, CHANNEL_KEYS) and not _pick(row, SKU_KEYS):
                    continue
                if not _pick(row, CHANNEL_KEYS):
                    row = dict(row)
                    row.setdefault("渠道", ch_hint.group(1))
                _merge_row(acc, row, window, days)
    return loaded


def _merge_row(
    acc: dict[tuple[str, str, str], dict[str, Any]],
    row: dict,
    window: str | None,
    window_days: int | None,
):
    sku = str(_pick(row, SKU_KEYS) or "").strip()
    if not sku:
        return
    channel = normalize_sku_channel_code(
        str(_pick(row, CHANNEL_KEYS) or ""),
        sku,
    ) or pd.sku_prefix(sku)
    region = str(_pick(row, REGION_KEYS) or "").strip()
    key = _norm_key(sku, channel, region)
    slot = acc.setdefault(
        key,
        {
            "sku": sku,
            "channel": channel or pd.sku_prefix(sku),
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


def load_sales_demand_index(
    region: str | None = None,
    *,
    data_dir: Path | str | None = None,
) -> tuple[dict[tuple[str, str, str], SalesDemandRecord], list[str]]:
    """
    读取 Data/Output 下 sales 8-30 / 15 / 30 文本或合并表（仅 CSV，不连库）。
    返回 (index, warnings)。
    """
    region_key = str(region or pd.default_region() or "NZ").strip().upper()
    if data_dir is None:
        _s, _d, _src, data_dir = pd.resolve_sources(region_key)
    data_dir = Path(data_dir or "")
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

    subdir_loaded = _load_per_channel_sales_dirs(data_dir, acc, warnings)
    loaded_any = loaded_any or subdir_loaded

    if not loaded_any:
        return {}, ["未找到 sales 8-30 / 15 / 30 导出（Output 根目录或渠道子文件夹）"]

    mode = os.getenv("INVENTORY_DEMAND_PICK", "v4_max").strip().lower()
    out: dict[tuple[str, str, str], SalesDemandRecord] = {}
    for key, slot in acc.items():
        row = slot.get("row") or {}
        windows = slot.get("windows") or {}
        avg, src = _pick_applied_daily(row, windows)
        if mode == "first_window":
            for window, days, _stems in WINDOWS:
                qty = windows.get(window)
                if qty is not None and qty > 0 and days:
                    avg, src = float(qty) / float(days), f"{window}天"
                    break

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
            demand_source=src or ("无销量" if avg <= 0 else "sales"),
            qty_windows={k: round(v, 4) for k, v in windows.items()},
            unit_volume_m3=vol,
            stockout_rate_pct=so_pct,
            raw_name=slot.get("name") or "",
        )
    return out, warnings


def collapse_demand_by_sku_channel(
    index: dict[tuple[str, str, str], SalesDemandRecord],
) -> dict[tuple[str, str], SalesDemandRecord]:
    """同一 SKU×渠道 多地区行：日均件数相加（对齐全国库存对全国销量）。保留体积/缺货来自任一行。"""
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for (_norm, ch, _reg), rec in (index or {}).items():
        key = (_norm, (ch or "").upper())
        slot = buckets.setdefault(
            key,
            {"units": 0.0, "sources": [], "rec": rec},
        )
        slot["units"] += float(rec.avg_daily_units or 0.0)
        if rec.demand_source and rec.demand_source not in ("无销量", ""):
            slot["sources"].append(rec.demand_source)
        if rec.unit_volume_m3 is not None and (slot["rec"].unit_volume_m3 is None):
            slot["rec"] = rec
    collapsed: dict[tuple[str, str], SalesDemandRecord] = {}
    for key, slot in buckets.items():
        rec = slot["rec"]
        src = " + ".join(dict.fromkeys(slot["sources"])) if slot["sources"] else rec.demand_source
        collapsed[key] = SalesDemandRecord(
            sku=rec.sku,
            channel=rec.channel,
            region="",
            avg_daily_units=round(float(slot["units"]), 6),
            demand_source=src or rec.demand_source,
            qty_windows=dict(rec.qty_windows),
            unit_volume_m3=rec.unit_volume_m3,
            stockout_rate_pct=rec.stockout_rate_pct,
            raw_name=rec.raw_name,
        )
    return collapsed
