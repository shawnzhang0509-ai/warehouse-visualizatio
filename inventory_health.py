"""库存健康 / 库存错配气泡图 — 仅读本地 Output/Data CSV（stock、sales、po.csv），不连接数据库。

库存健康 / 库存错配气泡图 — 基于现有 Output CSV（stock、weekly_sales、po）。

字段映射（不臆造列名，模糊匹配 panel_data 惯例）：
- SKU / 名称 / 分类：stock.csv → Sku, ProductName, ProductFamily
- 渠道：weekly_sales.Channel 或 LEFT(Sku,3)
- 日均消耗件数：weekly_sales TotalQty ÷ 统计天数（按 SKU×渠道 或 ×分店）
- 单件体积 m³：PriceRadarVolume / VolumeWithBox 等
- 在库体积：库存件数 × 单件体积；在途：po.csv 的 VolumeM3（或件数×体积）
- 缺货率：CSV 列 StockoutRate 等；若无则用「库存覆盖天数」代理（可配置，UI 标明）
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import panel_data as pd
from sales_demand import load_sales_demand_index

try:
    import warehouse_volume as wv
except ImportError:
    wv = None

ROOT_DIR = Path(__file__).resolve().parent

WEEKLY_STEMS = ("weekly_sales", "weekly_sales_export", "sales_weekly")
VOLUME_UNIT_KEYS = [
    "priceradarvolume", "price_radar_volume", "volumewithbox", "volume_with_box",
    "unitvolume", "unit_volume", "volume", "volumem3", "volume_m3",
]
STOCKOUT_KEYS = [
    "stockoutrate", "stockout_rate", "stock_out_rate", "oos_rate", "oos",
    "缺货率", "stockoutpct", "stockout_percent",
]
WEEKLY_QTY_KEYS = ["totalqty", "total_qty", "quantity", "qty", "salesqty", "sales_qty"]
WEEKLY_BRANCH_KEYS = ["branchname", "branch", "store", "storename", "location"]
WEEKLY_CHANNEL_KEYS = ["channel", "渠道", "sku_prefix"]
WEEKLY_SKU_KEYS = pd.CODE_KEYS
WEEKLY_START_KEYS = ["weekstart", "week_start", "startdate"]
WEEKLY_END_KEYS = ["weekend", "week_end", "enddate"]
SUPPLIER_KEYS = ["supplier", "vendor", "suppliername", "vendorname"]
BRAND_KEYS = ["brand", "brandname", "manufacturer"]

DEFAULT_LOOKBACK_DAYS = int(os.getenv("INVENTORY_HEALTH_LOOKBACK_DAYS", "90") or "90")
DEFAULT_STOCKOUT_X = float(os.getenv("INVENTORY_HEALTH_STOCKOUT_X", "50") or "50")
DEFAULT_DAYS_Y = float(os.getenv("INVENTORY_HEALTH_DAYS_Y", "60") or "60")
DEFAULT_COVER_DAYS_PROXY = float(os.getenv("INVENTORY_HEALTH_COVER_DAYS", "14") or "14")


@dataclass
class HealthThresholds:
    stockout_pct: float = DEFAULT_STOCKOUT_X
    consumption_days: float = DEFAULT_DAYS_Y
    cover_days_proxy: float = DEFAULT_COVER_DAYS_PROXY


@dataclass
class InventoryHealthRow:
    sku: str
    name: str
    category: str
    channel: str
    branch: str
    stockout_rate_pct: float | None
    stockout_source: str
    avg_daily_units: float
    unit_volume_m3: float | None
    avg_daily_demand_m3: float | None
    inventory_units: float
    inventory_volume_m3: float | None
    transit_volume_m3: float
    theoretical_days: float | None
    theoretical_days_label: str
    inventory_value: float | None
    priority: int
    quadrant: str
    incomplete: list[str] = field(default_factory=list)
    bubble_m3_day: float = 0.0
    demand_source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "name": self.name,
            "category": self.category,
            "channel": self.channel,
            "branch": self.branch,
            "stockout_rate_pct": self.stockout_rate_pct,
            "stockout_source": self.stockout_source,
            "avg_daily_units": self.avg_daily_units,
            "unit_volume_m3": self.unit_volume_m3,
            "avg_daily_demand_m3": self.avg_daily_demand_m3,
            "inventory_units": self.inventory_units,
            "inventory_volume_m3": self.inventory_volume_m3,
            "transit_volume_m3": self.transit_volume_m3,
            "theoretical_days": self.theoretical_days,
            "theoretical_days_label": self.theoretical_days_label,
            "inventory_value": self.inventory_value,
            "priority": self.priority,
            "quadrant": self.quadrant,
            "incomplete": self.incomplete,
            "bubble_m3_day": self.bubble_m3_day,
            "demand_source": self.demand_source,
        }


def _pick(row: dict, keys: list[str]):
    return pd._pick(row, keys)


def _lookup_sales_demand(index, norm: str, channel: str, branch: str):
    if not index:
        return None
    channel = (channel or "").upper()
    branch = (branch or "").strip()
    for reg in (branch, ""):
        hit = index.get((norm, channel, reg))
        if hit:
            return hit
    for key, rec in index.items():
        if key[0] == norm and key[1] == channel:
            return rec
    return None


def _demand_by_sku_channel(index) -> dict[tuple[str, str], Any]:
    """O(1) 查找：每个 SKU×渠道 保留日均需求最高的一条（避免对每个 SKU 扫全表）。"""
    out: dict[tuple[str, str], Any] = {}
    for (norm, ch, _reg), rec in (index or {}).items():
        key = (norm, (ch or "").upper())
        prev = out.get(key)
        if prev is None or (rec.avg_daily_units or 0) > (prev.avg_daily_units or 0):
            out[key] = rec
    return out


def _weekly_totals_by_sku_channel(
    buckets: dict[tuple[str, str, str], dict],
    branch_f: str,
) -> dict[tuple[str, str], dict]:
    """按 SKU×渠道 汇总 weekly_sales（分店可选），避免对每个 SKU 遍历 9 万行桶。"""
    out: dict[tuple[str, str], dict] = defaultdict(lambda: {"qty": 0.0, "stockout": []})
    branch_f = (branch_f or "").strip()
    for (norm, ch, branch), bucket in buckets.items():
        if branch_f and branch != branch_f:
            continue
        key = (norm, ch)
        slot = out[key]
        slot["qty"] += float(bucket.get("qty") or 0.0)
        slot["stockout"].extend(bucket.get("stockout") or [])
    return dict(out)


def _to_float(val, default=None):
    return pd._to_float(val) if val is not None else default


def _weekly_sales_path(region: str) -> Path | None:
    bundle = pd.get_region_bundle(region)
    data_dir = Path(bundle.get("data_dir") or "")
    found = pd._find_region_data_file(data_dir, WEEKLY_STEMS)
    return Path(found) if found else None


def _parse_week_date(row: dict) -> tuple[date | None, date | None]:
    start = _pick(row, WEEKLY_START_KEYS)
    end = _pick(row, WEEKLY_END_KEYS)
    for raw in (start, end):
        if not raw:
            continue
        text = str(raw).strip()[:10]
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
            try:
                return (datetime.strptime(text, fmt).date(), None)
            except ValueError:
                continue
    period = str(row.get("YearWeekPeriod") or row.get("yearweekperiod") or "")
    m = re.search(r"(\d{4}-\d{2}-\d{2})", period)
    if m:
        try:
            d0 = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            return d0, None
        except ValueError:
            pass
    return None, None


def _load_weekly_sales(region: str) -> tuple[list[dict], Path | None, str | None]:
    path = _weekly_sales_path(region)
    if not path or not path.is_file():
        return [], None, "未找到 weekly_sales.csv（请用 Data-NZ/weekly_sales SQL 导出到 Output 目录）"
    rows = pd._read_table(path)
    return rows, path, None


def _sales_aggregates(
    weekly_rows: list[dict],
    lookback_days: int,
) -> tuple[dict[tuple[str, str, str], dict], int, date | None, date | None]:
    """
    键：(sku_norm, channel, branch) → {qty, days_span, stockout_vals, weeks}
    branch 空串表示全店合计。
    """
    buckets: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"qty": 0.0, "stockout": [], "min_d": None, "max_d": None},
    )
    global_min: date | None = None
    global_max: date | None = None
    for row in weekly_rows:
        sku = str(_pick(row, WEEKLY_SKU_KEYS) or "").strip()
        if not sku:
            continue
        channel = str(_pick(row, WEEKLY_CHANNEL_KEYS) or pd.sku_prefix(sku) or "").strip().upper()
        branch = str(_pick(row, WEEKLY_BRANCH_KEYS) or "").strip()
        qty = _to_float(_pick(row, WEEKLY_QTY_KEYS), 0.0) or 0.0
        so = _pick(row, STOCKOUT_KEYS)
        d0, _d1 = _parse_week_date(row)
        if d0:
            global_min = d0 if global_min is None else min(global_min, d0)
            global_max = d0 if global_max is None else max(global_max, d0)
        key = (pd._norm_code(sku), channel, branch)
        b = buckets[key]
        b["qty"] += qty
        if so is not None and str(so).strip() != "":
            val = _to_float(so)
            if val is not None:
                b["stockout"].append(val if val <= 1 else val / 100.0)
        if d0:
            b["min_d"] = d0 if b["min_d"] is None else min(b["min_d"], d0)
            b["max_d"] = d0 if b["max_d"] is None else max(b["max_d"], d0)

    # 日均消耗：按配置的 lookback 天数摊平（与业务「近 N 天日均」一致）
    span_days = max(1, int(lookback_days or DEFAULT_LOOKBACK_DAYS))
    return buckets, span_days, global_min, global_max


def _unit_volume_m3(stock_row: dict) -> float | None:
    raw = _pick(stock_row, VOLUME_UNIT_KEYS)
    if raw is None:
        return None
    val = _to_float(raw)
    if val is None or val < 0:
        return None
    return val


def _stockout_proxy_pct(inventory_units: float, avg_daily_units: float, cover_days: float) -> float | None:
    if avg_daily_units <= 0:
        return None
    cover = inventory_units / avg_daily_units
    stress = 1.0 - min(1.0, cover / cover_days)
    return max(0.0, min(100.0, stress * 100.0))


def _theoretical_days(inv_vol: float | None, demand_m3: float | None) -> tuple[float | None, str]:
    if demand_m3 is None or demand_m3 <= 0:
        return None, "N/A"
    if inv_vol is None:
        return None, "N/A"
    if inv_vol <= 0:
        return 0.0, "0"
    return inv_vol / demand_m3, f"{inv_vol / demand_m3:.1f}"


def _quadrant(stockout: float | None, days: float | None, th: HealthThresholds) -> str:
    sx = stockout if stockout is not None else 0.0
    dy = days if days is not None else 0.0
    high_x = sx >= th.stockout_pct
    high_y = dy >= th.consumption_days
    if high_x and high_y:
        return "Inventory Mismatch"
    if high_x and not high_y:
        return "Supply Shortage"
    if not high_x and high_y:
        return "Potential Overstock"
    return "Healthy"


def _priority(stockout: float | None, demand_m3: float | None, days: float | None, th: HealthThresholds) -> int:
    sx = stockout or 0.0
    dm = demand_m3 or 0.0
    dy = days or 0.0
    if sx >= th.stockout_pct and dm > 0:
        return 1
    if sx >= th.stockout_pct and dy >= th.consumption_days:
        return 2
    if sx < th.stockout_pct and dy >= th.consumption_days:
        return 3
    return 4


def _load_po_by_sku(region: str) -> dict[str, float]:
    """在途体积：只读 Output-{region}/po.csv，绝不连数据库。"""
    if wv is None:
        return {}
    try:
        lines, _path = wv.load_po_lines_from_csv(region)
    except Exception:
        return {}
    out: dict[str, float] = defaultdict(float)
    for line in lines or []:
        sku = str(line.get("sku") or "").strip()
        if not sku:
            continue
        m3 = float(line.get("volume_m3") or 0.0)
        out[pd._norm_code(sku)] += m3
    return dict(out)


def build_inventory_health_report(
    region: str | None = None,
    *,
    channel: str = "",
    category: str = "",
    sku_filter: str = "",
    branch: str = "",
    supplier: str = "",
    brand: str = "",
    group_by: str = "sku",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    thresholds: HealthThresholds | None = None,
) -> dict[str, Any]:
    th = thresholds or HealthThresholds()
    region_key = str(region or pd.default_region() or "NZ").strip().upper()
    _stock_p, _disp_p, _src, data_dir = pd.resolve_sources(region_key)
    bundle = pd.get_region_bundle(region_key)
    demand_index, sales_warns = load_sales_demand_index(region_key, data_dir=data_dir)
    demand_by_sc = _demand_by_sku_channel(demand_index)
    always_weekly = os.getenv("INVENTORY_HEALTH_ALWAYS_WEEKLY", "").strip().lower() in ("1", "true", "yes")
    if demand_index and not always_weekly:
        weekly_rows, weekly_path, weekly_warn = [], None, None
        sales_buckets, span_days, d_min, d_max = {}, max(1, int(lookback_days or DEFAULT_LOOKBACK_DAYS)), None, None
        warnings_weekly_skip = "已用 sales 8-30/15/30，跳过 weekly_sales 大表以加速"
    else:
        weekly_rows, weekly_path, weekly_warn = _load_weekly_sales(region_key)
        sales_buckets, span_days, d_min, d_max = _sales_aggregates(weekly_rows, lookback_days)
        warnings_weekly_skip = ""

    stock_raw = bundle.get("stock_raw_rows")
    if not stock_raw:
        stock_path = bundle.get("stock_path") or _stock_p
        if stock_path and Path(stock_path).is_file():
            stock_raw = pd._read_table(stock_path)
    stock_raw = stock_raw or []
    po_by_sku = _load_po_by_sku(region_key)

    rows_out: list[InventoryHealthRow] = []
    warnings: list[str] = list(sales_warns)
    if warnings_weekly_skip:
        warnings.append(warnings_weekly_skip)
    if weekly_warn and not demand_index:
        warnings.append(weekly_warn)

    channel_f = channel.strip().upper()
    category_f = category.strip().lower()
    sku_f = sku_filter.strip().lower()
    branch_f = branch.strip()
    supplier_f = supplier.strip().lower()
    brand_f = brand.strip().lower()
    weekly_by_sc = _weekly_totals_by_sku_channel(sales_buckets, branch_f)

    for raw in stock_raw:
        if pd._is_discontinued(_pick(raw, pd.DISCONTINUE_KEYS)):
            continue
        code = str(_pick(raw, pd.CODE_KEYS) or "").strip()
        if not code:
            continue
        norm = pd._norm_code(code)
        ch = pd.sku_prefix(code)
        if channel_f and ch != channel_f:
            continue
        cat = str(_pick(raw, pd.FAMILY_KEYS) or "未分类").strip()
        if category_f and category_f not in cat.lower():
            continue
        if sku_f and sku_f not in code.lower():
            continue
        sup = str(_pick(raw, SUPPLIER_KEYS) or "").strip()
        if supplier_f and supplier_f not in sup.lower():
            continue
        br = str(_pick(raw, BRAND_KEYS) or "").strip()
        if brand_f and brand_f not in br.lower():
            continue

        wh_stock = pd._extract_warehouse_stock(raw)
        inv_units = sum(wh_stock.values()) if wh_stock else (_to_float(_pick(raw, pd.STOCK_KEYS), 0.0) or 0.0)
        unit_vol = _unit_volume_m3(raw)
        inv_vol = inv_units * unit_vol if unit_vol is not None else None
        price = _to_float(_pick(raw, pd.PRICE_KEYS))
        inv_value = inv_units * price if price is not None and inv_units > 0 else None
        transit_m3 = float(po_by_sku.get(norm, 0.0))

        demand_source = ""
        stockout_vals: list[float] = []
        demand_rec = _lookup_sales_demand(demand_index, norm, ch, branch_f)
        if demand_rec is None:
            demand_rec = demand_by_sc.get((norm, ch))
        if demand_rec and demand_rec.avg_daily_units > 0:
            avg_daily = demand_rec.avg_daily_units
            demand_source = demand_rec.demand_source
            if demand_rec.unit_volume_m3 is not None and unit_vol is None:
                unit_vol = demand_rec.unit_volume_m3
            if demand_rec.stockout_rate_pct is not None:
                stockout_vals = [demand_rec.stockout_rate_pct / 100.0]
        else:
            sale = sales_buckets.get((norm, ch, branch_f)) if branch_f else None
            if sale is None:
                tot = weekly_by_sc.get((norm, ch))
                if tot:
                    qty_sum = float(tot.get("qty") or 0.0)
                    stockout_vals = list(tot.get("stockout") or [])
                else:
                    qty_sum = 0.0
                avg_daily = qty_sum / span_days if span_days > 0 else 0.0
                demand_source = f"weekly_sales/{span_days}d"
            else:
                avg_daily = sale["qty"] / span_days if span_days > 0 else 0.0
                stockout_vals = sale["stockout"]
                demand_source = f"weekly_sales/{span_days}d"

        incomplete: list[str] = []
        if unit_vol is None:
            incomplete.append("缺少单件体积(PriceRadarVolume/VolumeWithBox/体积系数)")
        if avg_daily <= 0:
            incomplete.append("无销量(sales 8-30/15/30 或 weekly_sales)")

        stockout_pct: float | None = None
        stockout_source = "missing"
        if stockout_vals:
            stockout_pct = sum(stockout_vals) / len(stockout_vals) * 100.0
            stockout_source = "column"
        elif avg_daily > 0:
            proxy = _stockout_proxy_pct(inv_units, avg_daily, th.cover_days_proxy)
            if proxy is not None:
                stockout_pct = proxy
                stockout_source = f"proxy_{int(th.cover_days_proxy)}d_cover"

        demand_m3 = avg_daily * unit_vol if unit_vol is not None and avg_daily > 0 else None
        th_days, th_label = _theoretical_days(inv_vol, demand_m3)
        if avg_daily <= 0:
            th_label = "∞" if inv_vol and inv_vol > 0 else "N/A"
            th_days = None

        quad = _quadrant(stockout_pct, th_days, th)
        pri = _priority(stockout_pct, demand_m3, th_days, th)
        bubble = demand_m3 or 0.0

        rows_out.append(
            InventoryHealthRow(
                sku=code,
                name=str(_pick(raw, pd.NAME_KEYS) or "").strip(),
                category=cat,
                channel=ch,
                branch=branch_f or "（全渠道/分店合计）",
                stockout_rate_pct=round(stockout_pct, 2) if stockout_pct is not None else None,
                stockout_source=stockout_source,
                avg_daily_units=round(avg_daily, 4),
                unit_volume_m3=unit_vol,
                avg_daily_demand_m3=round(demand_m3, 4) if demand_m3 is not None else None,
                inventory_units=inv_units,
                inventory_volume_m3=round(inv_vol, 4) if inv_vol is not None else None,
                transit_volume_m3=round(transit_m3, 4),
                theoretical_days=round(th_days, 2) if th_days is not None else None,
                theoretical_days_label=th_label,
                inventory_value=round(inv_value, 2) if inv_value is not None else None,
                priority=pri,
                quadrant=quad,
                incomplete=incomplete,
                bubble_m3_day=round(bubble, 4),
                demand_source=demand_source,
            )
        )

    if group_by and group_by != "sku":
        rows_out = _aggregate_rows(rows_out, group_by, th)

    summary = _summarize(rows_out, th)
    return {
        "region": region_key,
        "rows": [r.as_dict() for r in rows_out],
        "summary": summary,
        "thresholds": {
            "stockout_pct": th.stockout_pct,
            "consumption_days": th.consumption_days,
            "cover_days_proxy": th.cover_days_proxy,
        },
        "meta": {
            "weekly_sales_path": str(weekly_path) if weekly_path else None,
            "sales_span_days": span_days,
            "sales_date_min": str(d_min) if d_min else None,
            "sales_date_max": str(d_max) if d_max else None,
            "stockout_note": (
                "缺货率优先读 weekly_sales/stock 的 StockoutRate 列；"
                f"否则用 {int(th.cover_days_proxy)} 天需求覆盖代理（非历史缺货天数）"
            ),
            "formulas": {
                "avg_daily_demand_m3": "采用需求(件/天) × 单件体积(m³)；或 sales 窗口销量÷天数",
                "theoretical_days": "在库体积(m³) ÷ 日均需求体积(m³/天)",
                "stocking_volume": "备货体积(m³) = 采用需求 × 体积系数（与供应链决策一致）",
            },
            "sales_demand_rows": len(demand_index),
        },
        "warnings": warnings,
    }


def _aggregate_rows(rows: list[InventoryHealthRow], group_by: str, th: HealthThresholds) -> list[InventoryHealthRow]:
    key_field = {
        "channel": "channel",
        "category": "category",
        "supplier": "category",
        "brand": "category",
        "warehouse": "branch",
    }.get(group_by, "channel")
    # supplier/brand need raw - skip if not on row; use category fallback label
    groups: dict[str, list[InventoryHealthRow]] = defaultdict(list)
    for r in rows:
        if group_by == "channel":
            k = r.channel
        elif group_by == "category":
            k = r.category
        else:
            k = getattr(r, key_field, r.channel) or "?"
        groups[k].append(r)

    out: list[InventoryHealthRow] = []
    for label, items in groups.items():
        inv_vol = sum(x.inventory_volume_m3 or 0 for x in items)
        demand = sum(x.avg_daily_demand_m3 or 0 for x in items)
        inv_u = sum(x.inventory_units for x in items)
        transit = sum(x.transit_volume_m3 for x in items)
        avg_daily_u = sum(x.avg_daily_units for x in items)
        so_weighted = []
        for x in items:
            w = x.avg_daily_demand_m3 or x.avg_daily_units or 1.0
            if x.stockout_rate_pct is not None:
                so_weighted.append((x.stockout_rate_pct, w))
        stockout = None
        if so_weighted:
            tw = sum(w for _, w in so_weighted)
            stockout = sum(v * w for v, w in so_weighted) / tw if tw else None
        th_days, th_label = _theoretical_days(inv_vol if inv_vol else None, demand if demand else None)
        rep = items[0]
        out.append(
            InventoryHealthRow(
                sku=f"Σ {label}",
                name=f"{group_by} 汇总 · {len(items)} SKU",
                category=rep.category,
                channel=rep.channel if group_by != "channel" else label,
                branch=rep.branch,
                stockout_rate_pct=round(stockout, 2) if stockout is not None else None,
                stockout_source="aggregated",
                avg_daily_units=round(avg_daily_u, 4),
                unit_volume_m3=None,
                avg_daily_demand_m3=round(demand, 4) if demand else None,
                inventory_units=inv_u,
                inventory_volume_m3=round(inv_vol, 4) if inv_vol else None,
                transit_volume_m3=round(transit, 4),
                theoretical_days=round(th_days, 2) if th_days is not None else None,
                theoretical_days_label=th_label,
                inventory_value=sum(x.inventory_value or 0 for x in items) or None,
                priority=_priority(stockout, demand, th_days, th),
                quadrant=_quadrant(stockout, th_days, th),
                incomplete=[],
                bubble_m3_day=round(demand, 4),
            )
        )
    return out


def _summarize(rows: list[InventoryHealthRow], th: HealthThresholds) -> dict[str, Any]:
    inv_vol = sum(r.inventory_volume_m3 or 0 for r in rows)
    demand = sum(r.avg_daily_demand_m3 or 0 for r in rows)
    th_days, _ = _theoretical_days(inv_vol, demand)
    so_vals = [r.stockout_rate_pct for r in rows if r.stockout_rate_pct is not None]
    avg_so = sum(so_vals) / len(so_vals) if so_vals else None
    high_so = sum(1 for r in rows if (r.stockout_rate_pct or 0) >= th.stockout_pct)
    mismatch = sum(1 for r in rows if r.quadrant == "Inventory Mismatch")
    inv_val = sum(r.inventory_value or 0 for r in rows)
    return {
        "total_inventory_volume_m3": round(inv_vol, 2),
        "total_avg_daily_demand_m3": round(demand, 4),
        "overall_theoretical_days": round(th_days, 2) if th_days is not None else None,
        "overall_stockout_rate_pct": round(avg_so, 2) if avg_so is not None else None,
        "high_stockout_sku_count": high_so,
        "inventory_mismatch_count": mismatch,
        "inventory_value": round(inv_val, 2) if inv_val else None,
        "sku_count": len(rows),
    }
