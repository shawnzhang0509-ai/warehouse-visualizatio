"""有货未展示看板 —— 纯本地数据层（不依赖任何网页/Flask）。

核心：用店面展示数据 (display) 比对 non-discontinue 且有货的产品 (stock)，
按【店面】逐个产品给出：有没有货、在该店面有没有展示、图片。

数据来源（按优先级）
1. 环境变量 INSTOCK_STOCK_CSV / INSTOCK_DISPLAY_CSV
2. INSTOCK_DATA_DIR 目录下的 stock.csv / display.csv
3. INSTOCK_REGION（如 NZ/CA）→ Output-{region}/ 下的 stock/display 文件

支持 CSV；列名不需要完全一致，_pick 会做模糊匹配。
"""

import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

try:
    from openpyxl import load_workbook as _load_workbook
except ImportError:
    _load_workbook = None

ROOT_DIR = Path(__file__).parent
RUNNER_CONFIG_FILE = ROOT_DIR / "region_runner_config.json"
EXEMPTION_CONFIG_FILE = ROOT_DIR / "family_exemption.json"

# 看板地区下拉固定顺序；即使用户 region_runner_config.json 较旧也会显示三国入口
REGION_ORDER = ("NZ", "AU", "CA")
DEFAULT_REGION_META = {
    "NZ": {"label": "新西兰", "template_dir": "Data-NZ", "output_dir": "Output-NZ"},
    "AU": {"label": "澳洲", "template_dir": "Data-AU", "output_dir": "Output-AU"},
    "CA": {"label": "加拿大", "template_dir": "Data-CA", "output_dir": "Output-CA"},
}
REGION_IMAGE_BASE = {
    "NZ": "https://ierpapi.ifurniture.co.nz/",
    "AU": "https://ierpapi.ifurniture.com.au/",
    "CA": "https://ierpapi.ifurniture.ca/",
}
IMAGE_HOST_ALTERNATES = {
    "ierpapi.ifurniture.co.nz": ("www.ifurniture.co.nz", "cdn.ifurniture.co.nz"),
    "ierpapi.ifurniture.com.au": ("www.ifurniture.com.au",),
    "ierpapi.ifurniture.ca": ("www.ifurniture.ca",),
}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

# 启动时预加载停产 SKU（默认关闭，先秒开在产；设 PANEL_EAGER_DISCONTINUED=1 可恢复旧行为）
EAGER_DISCONTINUED_STOCK = os.getenv("PANEL_EAGER_DISCONTINUED", "0").strip().lower() in (
    "1", "true", "yes", "on",
)

CODE_KEYS = ["productcode", "product_code", "sku", "itemcode", "item_code",
             "code", "productid", "product_id", "product", "item"]
BLACKLIST_STEMS = ["blacklist", "sku_blacklist", "black_list", "product_blacklist"]
BLACKLIST_KEYS = ["sku", "blacklist", "blacklistsku"] + CODE_KEYS
CHANNEL_OWNER_STEMS = ["channel_owner", "channel_owners", "sku_owners", "owner_channels"]
CHANNEL_OWNER_HEADER_PAIRS = (
    ("owner", "channel"),
    ("负责人", "渠道"),
    ("owner", "渠道"),
    ("负责人", "channel"),
)
OWNER_KEYS = ["owner", "负责人", "person", "personincharge"]
CHANNEL_KEYS = ["channel", "渠道", "sku_prefix", "prefix", "channelcode"]
SUB_CHANNEL_KEYS = ["sub_channel", "二级渠道", "subchannel", "channel2", "二级", "subchannelcode"]
PARTS_PARENT_KEYS = [
    "parentsku", "parent_sku", "kitsku", "kit_sku", "productsku", "product_sku",
    "productsk", "productcc", "productcode",
    "mainsku", "main_sku", "成品sku", "母件sku", "setsku", "kitcode",
]
PARTS_COMPONENT_KEYS = [
    "partsku", "part_sku", "componentsku", "component_sku", "subsku", "子件sku",
    "componentcode", "partcode",
]
PARTS_PER_SET_KEYS = [
    "partsperset", "parts_per_set", "qtyperset", "unitqty", "部件数", "requiredqty",
    "标准部件", "标准部件数",
]
WIDE_PART_NAME_KEYS = ["partname", "part_name", "部件名", "部件名称", "part"]
TRANSFER_WAREHOUSE_BUCKETS = (
    ("carbine", (
        "carbine", "carbine rd", "carbin", "carbin rd", "cbn", "carbine road",
        "carbine warehouse", "carbine wh",
    )),
    ("walls", (
        "walls", "wall rd", "walls road", "walls in transit", "wall warehouse",
        "walls wh", "walls transit",
    )),
    ("chch", (
        "gerald", "gerald connelly", "geraldconnelly", "gerald connolly",
        "gc", "g c", "chch", "connelly", "christchurch", "chc warehouse",
        "south island", "southisland",
    )),
)
PARTS_KIT_FILE_STEMS = ("parts_kits", "parts_kit", "kit_parts", "parts_bom")
WAREHOUSE_BUCKET_FILE_STEMS = ("warehouse_buckets", "warehouse_map", "warehouse_aliases")
LEAD_TIME_KEYS = ["lead_time", "leadtime", "leadtime_days", "lead timedays"]
MERGE_PRODUCT_KEYS = ["merge_products", "merge", "mergeproducts", "必须合并计算的产品"]
MERGE_REGION_KEYS = ["merge_regions", "regions", "merge_regions", "必须合并计算的地区"]
OWNER_NOTE_KEYS = ["note", "remark", "split_reason", "拆分原因", "备注"]
MERGE_ALL_TOKENS = frozenset({"所有", "all", "ALL", "*", ""})
NAME_KEYS = ["productname", "product_name", "name", "description", "desc", "title"]
FAMILY_KEYS = ["family", "productfamily", "product_family", "category",
               "categoryname", "category_name", "group", "producttype", "type",
               "productfamilyname"]
STOCK_KEYS = ["stockqty", "stock_qty", "stock", "qty", "quantity", "onhand",
              "on_hand", "onhandqty", "on_hand_qty", "qtyonhand", "qty_on_hand",
              "available", "availableqty", "available_qty", "availablestock",
              "soh", "totalstock", "total_stock"]
PRICE_KEYS = ["price", "unitprice", "unit_price", "sellprice", "sell_price",
              "saleprice", "sale_price", "retailprice", "retail_price"]
DISCONTINUE_KEYS = ["discontinued", "isdiscontinued", "is_discontinued", "isdisconti",
                    "discontinue", "discontinueflag", "discontinue_flag", "status"]
STORE_KEYS = ["store", "storename", "store_name", "warehouse", "warehousename",
              "warehouse_name", "location", "branch", "shop", "displaywarehouse",
              "display_warehouse", "site"]
IMAGE_KEYS = ["imagefile", "image", "imageurl", "image_url", "img",
              "picture", "photo", "thumbnail", "thumb"]

ALL_STORES = "全部店面"
UNCATEGORIZED_FAMILIES = frozenset({"", "未分类", "UNCATEGORIZED", "N/A", "NONE", "未知"})

# 店面展示名 → 库存列（stock.xlsx 多仓交叉读取）
WAREHOUSE_LABELS = {
    "carbine": "Carbine",
    "walls": "Walls",
    "geraldconnelly": "GC",
    "northisland": "North Island",
}
REGION_STORE_STOCK_RULES = {
    "NZ": [
        (("onehunga", "westgate", "hamilton", "sleeplab"), ("carbine", "walls")),
        (("chch", "christchurch", "gerald", "treffers", "presale"), ("geraldconnelly",)),
    ],
}
DEFAULT_ALL_WAREHOUSES = ("carbine", "walls", "geraldconnelly")
NZ_NORTH_WAREHOUSES = ("carbine", "walls")
NZ_SOUTH_WAREHOUSES = ("geraldconnelly",)
ISLAND_STOCK_CLASSES = {
    "both": "南北都有",
    "south_only": "南有北无",
    "north_only": "北有南无",
    "none": "南北都没",
}
ISLAND_STOCK_GRID = (
    ("both", "south_only"),
    ("north_only", "none"),
)
STORE_NAME_SKIP_TOKENS = frozenset({
    "shop", "display", "store", "warehouse", "wh", "the", "for", "and", "repair", "outlet",
})
METADATA_COLUMN_KEYS = frozenset({
    "productcode", "product_code", "sku", "itemcode", "item_code", "code", "productid",
    "product_id", "product", "item", "productname", "product_name", "name", "description",
    "desc", "title", "family", "productfamily", "product_family", "category", "categoryname",
    "category_name", "group", "producttype", "type", "productfamilyname", "price", "unitprice",
    "unit_price", "sellprice", "sell_price", "saleprice", "sale_price", "retailprice",
    "retail_price", "discontinued", "isdiscontinued", "is_discontinued", "discontinue",
    "discontinueflag", "discontinue_flag", "status", "imagefile", "image", "imageurl",
    "image_url", "img", "picture", "photo", "thumbnail", "thumb", "barcode", "ean", "upc",
    "vendor", "supplier", "brand", "currency", "tax", "weight", "width", "height", "depth",
    "length", "color", "colour", "material", "url", "note", "notes", "remark", "remarks",
})
WAREHOUSE_COLUMN_SKIP_KEYS = frozenset({
    "total", "grandtotal", "sum", "overall", "subtotal", "northislandtotal", "southislandtotal",
})

# region_key -> cached bundle (invalidated when file mtime changes)
_REGION_CACHE = {}
_STORE_VIEW_CACHE = {}
_EXEMPTION_CONFIG_CACHE = None


def _strip_cell_bom(value):
    text = str(value).strip()
    if text.startswith("\ufeff"):
        text = text[1:].strip()
    return text


def _pick(row, keys):
    lower = {_strip_cell_bom(k).lower(): v for k, v in row.items()}
    for key in keys:
        if key in lower:
            value = lower[key]
            if value is not None and str(value).strip() != "":
                return value
    return None


def _to_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def _is_discontinued(value):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return float(value) != 0
        except (TypeError, ValueError):
            pass
    text = str(value).strip().lower()
    if text in ("", "0", "0.0", "false", "no", "n", "active", "live"):
        return False
    if text in ("1", "1.0", "true", "yes", "y"):
        return True
    return "discontinue" in text


def _norm_code(value):
    return str(value).strip().upper() if value is not None else ""


def _norm_col_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name).strip().lower())


def _classify_warehouse_column(col_name):
    """把 stock 列名映射到标准仓名（CarbineSt / WallsStoc / GeraldConnellyStock 等）。"""
    key = _norm_col_key(col_name)
    if not key:
        return None
    if "carbine" in key:
        return "carbine"
    if "walls" in key:
        return "walls"
    if "geraldconnelly" in key or key.startswith("gc"):
        return "geraldconnelly"
    if "northisland" in key or key.startswith("northislar"):
        return "northisland"
    return None


def _is_metadata_column(col_name):
    key = _norm_col_key(col_name)
    if not key:
        return True
    if key in METADATA_COLUMN_KEYS:
        return True
    return any(key.startswith(prefix) for prefix in ("product", "image", "isdiscontinued"))


def _warehouse_key_from_column(col_name):
    """识别各仓库存列；NZ 用标准仓名，CA/AU 等用列名归一化后的 key（如 calgarystock）。"""
    if _is_metadata_column(col_name):
        return None
    key = _norm_col_key(col_name)
    if not key or key in WAREHOUSE_COLUMN_SKIP_KEYS:
        return None
    if any(skip in key for skip in WAREHOUSE_COLUMN_SKIP_KEYS):
        return None

    known = _classify_warehouse_column(col_name)
    if known:
        return known

    stock_markers = ("stock", "qty", "quantity", "onhand", "soh", "available")
    if any(marker in key for marker in stock_markers):
        return key
    return None


def _warehouse_label(warehouse_key):
    label = WAREHOUSE_LABELS.get(warehouse_key)
    if label:
        return label
    text = re.sub(r"(stock|qty|quantity|onhand|soh|available)$", "", warehouse_key, flags=re.I)
    if not text:
        return warehouse_key
    return text[:1].upper() + text[1:]


def _extract_warehouse_stock(row):
    """从一行 stock 数据提取各仓库存数量。"""
    stock = {}
    for col, value in row.items():
        wh = _warehouse_key_from_column(col)
        if not wh:
            continue
        qty = _to_float(value) or 0.0
        stock[wh] = stock.get(wh, 0.0) + qty
    return stock


def _store_name_tokens(store_name):
    text = re.sub(r"[^a-z0-9]+", " ", str(store_name).strip().lower())
    return [t for t in text.split() if t and t not in STORE_NAME_SKIP_TOKENS and len(t) >= 3]


def _collect_catalog_warehouse_keys(products):
    keys = set()
    for product in products:
        keys.update((product.get("warehouse_stock") or {}).keys())
    return keys


def _warehouses_for_store(store_name, region_key, catalog_keys=None):
    """根据所选店面，决定用哪些仓库列计算有货数量。"""
    catalog = tuple(catalog_keys or ())
    if store_name == ALL_STORES:
        if catalog:
            return catalog
        if region_key.upper() == "NZ":
            return DEFAULT_ALL_WAREHOUSES
        return catalog

    text = str(store_name).strip().lower()
    for patterns, warehouses in REGION_STORE_STOCK_RULES.get(region_key.upper(), []):
        if any(p in text for p in patterns):
            return warehouses

    if catalog:
        matched = []
        for token in _store_name_tokens(store_name):
            for wh in catalog:
                if token in wh.lower() and wh not in matched:
                    matched.append(wh)
        if matched:
            return tuple(matched)

    if region_key.upper() == "NZ":
        if any(x in text for x in ("auck", "onehunga", "westgate", "hamilton", "north")):
            return ("carbine", "walls")
        if any(x in text for x in ("chch", "christ", "gerald", "treffers", "south")):
            return ("geraldconnelly",)
        return DEFAULT_ALL_WAREHOUSES

    if catalog:
        return catalog
    return DEFAULT_ALL_WAREHOUSES


def _qty_from_warehouses(warehouse_stock, warehouse_keys):
    return sum(float(warehouse_stock.get(k, 0) or 0) for k in warehouse_keys)


def island_stock_supported(region_key):
    return str(region_key or "").upper() == "NZ"


def classify_island_stock(warehouse_stock, region_key="NZ"):
    """按北岛(Carbine+Walls) / 南岛(GC) 库存分为四象限。"""
    if not island_stock_supported(region_key):
        return None, 0, 0, ""
    north = _qty_from_warehouses(warehouse_stock or {}, NZ_NORTH_WAREHOUSES)
    south = _qty_from_warehouses(warehouse_stock or {}, NZ_SOUTH_WAREHOUSES)
    north_ok = north > 0
    south_ok = south > 0
    if north_ok and south_ok:
        key = "both"
    elif south_ok:
        key = "south_only"
    elif north_ok:
        key = "north_only"
    else:
        key = "none"
    return key, int(north), int(south), ISLAND_STOCK_CLASSES[key]


def _enrich_island_stock(item, region_key):
    warehouse_stock = item.get("warehouse_stock") or {}
    cls, north_q, south_q, label = classify_island_stock(warehouse_stock, region_key)
    item["island_stock_class"] = cls
    item["north_stock_qty"] = north_q
    item["south_stock_qty"] = south_q
    item["island_stock_label"] = label
    return item


def _island_percents(counts):
    total = sum(counts.values())
    if not total:
        return {key: 0.0 for key in ISLAND_STOCK_CLASSES}
    return {key: round(counts[key] / total * 100, 1) for key in ISLAND_STOCK_CLASSES}


def _island_counts_from_products(products, include_discontinued=False):
    counts = {key: 0 for key in ISLAND_STOCK_CLASSES}
    rows = {key: [] for key in ISLAND_STOCK_CLASSES}
    for product in products:
        if not include_discontinued and product.get("discontinued"):
            continue
        cls = product.get("island_stock_class")
        if not cls:
            continue
        counts[cls] += 1
        rows[cls].append(product)
    return counts, rows


def products_for_channel_config(products, cfg, include_discontinued=False):
    """与负责人报表一致的渠道 SKU 匹配。"""
    active = [p for p in products if include_discontinued or not p.get("discontinued")]
    matched = [p for p in active if product_matches_channel(p.get("code", ""), cfg["channel"])]
    if not matched:
        return []
    sub = str(cfg.get("sub_channel") or "").strip()
    if sub:
        matched = [p for p in matched if product_matches_channel(p.get("code", ""), sub)]
    if not matched:
        return []
    if not _is_merge_all(cfg["merge_products"]):
        tokens = [t.strip() for t in cfg["merge_products"].split("+") if t.strip()]
        matched = [p for p in matched if any(_token_in_product(t, p) for t in tokens)]
    return matched


def resolve_product_owner_channel(product, config_rows):
    for cfg in config_rows or []:
        if products_for_channel_config([product], cfg):
            return cfg["owner"], cfg["channel"]
    return "", ""


def list_owner_channel_tree(config_rows):
    """负责人 → 渠道 二级结构（用于级联筛选）。"""
    owners = sorted({cfg["owner"] for cfg in (config_rows or []) if cfg.get("owner")})
    channels_by_owner = {}
    for cfg in config_rows or []:
        owner = cfg.get("owner")
        channel = cfg.get("channel")
        if not owner or not channel:
            continue
        channels_by_owner.setdefault(owner, set()).add(str(channel))
    channels_by_owner = {
        owner: sorted(channels, key=str)
        for owner, channels in channels_by_owner.items()
    }
    return owners, channels_by_owner


def channels_for_owner(config_rows, owner=None):
    """返回某负责人下的渠道列表；未选负责人时返回空（需先选一级）。"""
    if not owner or owner in ("", "全部负责人"):
        return []
    _, channels_by_owner = list_owner_channel_tree(config_rows)
    return list(channels_by_owner.get(owner, []))


def filter_products_by_owner_channel(products, config_rows, owner=None, channel=None,
                                     sub_channel=None, include_discontinued=False):
    """按负责人 / 一级渠道 / 二级渠道筛选 SKU。"""
    if not config_rows:
        scoped = list(products)
    else:
        owner = str(owner or "").strip()
        channel = str(channel or "").strip()
        sub_channel = str(sub_channel or "").strip()
        if (
            owner in ("", "全部负责人")
            and channel in ("", "全部渠道")
            and sub_channel in ("", "全部二级渠道")
        ):
            scoped = list(products)
        else:
            codes = set()
            for cfg in config_rows:
                if owner and owner not in ("全部负责人",) and cfg["owner"] != owner:
                    continue
                if channel and channel not in ("全部渠道",) and cfg["channel"] != channel:
                    continue
                if sub_channel and sub_channel not in ("全部二级渠道",):
                    cfg_sub = str(cfg.get("sub_channel") or "").strip()
                    if cfg_sub and cfg_sub != sub_channel:
                        continue
                for product in products_for_channel_config(products, cfg, include_discontinued):
                    codes.add(product.get("norm_code") or product.get("code"))
            scoped = [
                p for p in products
                if (p.get("norm_code") or p.get("code")) in codes
            ]
    sub_channel = str(sub_channel or "").strip()
    if sub_channel and sub_channel not in ("", "全部二级渠道"):
        scoped = [
            p for p in scoped
            if product_matches_channel(p.get("code", ""), sub_channel)
            or sku_prefix(p.get("code", "")) == sub_channel
        ]
    return scoped


def filter_island_scope(products, config_rows=None, owner=None, channel=None,
                        include_discontinued=False):
    """南北岛页：负责人（channel_owners 规则）+ 渠道（SKU 前三位，如 130、830）。"""
    scoped = list(products)
    if owner and owner not in ("", "全部负责人"):
        if config_rows:
            scoped = filter_products_by_owner_channel(
                products, config_rows, owner=owner, channel=None, sub_channel=None,
                include_discontinued=include_discontinued,
            )
        else:
            scoped = list(products)
    if channel and channel not in ("", "全部渠道"):
        ch = str(channel).strip()
        scoped = [
            p for p in scoped
            if product_matches_channel(p.get("code", ""), ch)
            or sku_prefix(p.get("code", "")) == ch
            or sku_prefix(p.get("code", "")) == ch[:SKU_PREFIX_LEN]
        ]
    if not include_discontinued:
        scoped = [p for p in scoped if not p.get("discontinued")]
    return scoped


def list_island_channel_options(products, config_rows, owner=None, include_discontinued=False):
    """负责人下可选渠道 = 其 SKU 的前三位汇总（与产品表「渠道」列一致）。"""
    if not owner or owner in ("", "全部负责人"):
        return []
    scoped = filter_island_scope(
        products, config_rows, owner=owner, channel=None,
        include_discontinued=include_discontinued,
    )
    return sorted({
        sku_prefix(p.get("code", ""))
        for p in scoped
        if p.get("code") and sku_prefix(p.get("code", "")) not in ("", "???")
    })


def list_secondary_channels_for_scope(products, config_rows, owner=None, channel=None,
                                      include_discontinued=False):
    """二级渠道：优先 channel_owners 的 sub_channel 列，否则用 SKU 前三位。"""
    owner = str(owner or "").strip()
    channel = str(channel or "").strip()
    if owner in ("", "全部负责人"):
        return []
    subs = set()
    for cfg in config_rows or []:
        if cfg.get("owner") != owner:
            continue
        if channel and channel not in ("", "全部渠道") and cfg.get("channel") != channel:
            continue
        sc = str(cfg.get("sub_channel") or "").strip()
        if sc:
            subs.add(sc)
    if subs:
        return sorted(subs, key=str)
    scoped = filter_products_by_owner_channel(
        products, config_rows, owner=owner, channel=channel or None,
        include_discontinued=include_discontinued,
    )
    return sorted({
        sku_prefix(p.get("code", ""))
        for p in scoped
        if p.get("code") and sku_prefix(p.get("code", ""))
    })


def aggregate_island_quadrants(products, include_discontinued=False, owner=None, channel=None,
                               sub_channel=None, config_rows=None):
    """统计四象限 SKU 数（默认仅计在产），可按负责人/渠道筛选。"""
    ch = channel or sub_channel
    scoped = products
    if owner or ch:
        scoped = filter_island_scope(
            products, config_rows, owner=owner, channel=ch,
            include_discontinued=include_discontinued,
        )
    counts, rows = _island_counts_from_products(scoped, include_discontinued)
    return {
        "counts": counts,
        "rows": rows,
        "percents": _island_percents(counts),
        "total": sum(counts.values()),
    }


def aggregate_island_quadrants_grouped(products, group_by, config_rows, include_discontinued=False):
    """按负责人或渠道生成多组四象限统计（含占比）。"""
    groups = []
    if not config_rows:
        return groups
    if group_by == "channel":
        for cfg in config_rows:
            matched = products_for_channel_config(products, cfg, include_discontinued)
            if not matched:
                continue
            counts, _ = _island_counts_from_products(matched, include_discontinued)
            total = sum(counts.values())
            groups.append({
                "key": cfg["channel"],
                "label": str(cfg["channel"]),
                "owner": cfg["owner"],
                "channel": cfg["channel"],
                "counts": counts,
                "percents": _island_percents(counts),
                "total": total,
            })
    else:
        by_owner = {}
        for cfg in config_rows:
            matched = products_for_channel_config(products, cfg, include_discontinued)
            bucket = by_owner.setdefault(cfg["owner"], {})
            for product in matched:
                code = product.get("norm_code") or product.get("code")
                if code not in bucket:
                    bucket[code] = product
        for owner, prod_map in by_owner.items():
            matched = list(prod_map.values())
            counts, _ = _island_counts_from_products(matched, include_discontinued)
            total = sum(counts.values())
            groups.append({
                "key": owner,
                "label": owner,
                "owner": owner,
                "channel": "",
                "counts": counts,
                "percents": _island_percents(counts),
                "total": total,
            })
    groups.sort(key=lambda item: (-item["total"], str(item["label"]).lower()))
    return groups


def _stock_breakdown(warehouse_stock, warehouse_keys):
    parts = []
    for key in warehouse_keys:
        qty = int(warehouse_stock.get(key, 0) or 0)
        if qty > 0:
            parts.append(f"{_warehouse_label(key)} {qty}")
    return " + ".join(parts) if parts else ""


def _apply_store_stock(product, store, region_key, warehouse_keys=None):
    warehouse_stock = product.get("warehouse_stock") or {}
    warehouse_keys = warehouse_keys or _warehouses_for_store(store, region_key)
    if warehouse_stock:
        qty = _qty_from_warehouses(warehouse_stock, warehouse_keys)
        breakdown = _stock_breakdown(warehouse_stock, warehouse_keys)
    else:
        qty = float(product.get("stock_qty") or 0)
        breakdown = ""
    item = product.copy()
    item.update({
        "stock_qty": qty,
        "in_stock": qty > 0,
        "stock_warehouses": warehouse_keys,
        "stock_breakdown": breakdown,
    })
    return item


def _read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_table(path):
    path = Path(path)
    if path.suffix.lower() != ".csv":
        raise ValueError(f"看板仅支持 CSV 数据文件，请重新执行 SQL 导出：{path}")
    return _read_csv(path)


def _file_mtime(path):
    p = Path(path)
    return p.stat().st_mtime if p.is_file() else 0.0


def normalize_url(url):
    """把含中文/空格的 URL 编码成可请求的地址。"""
    text = str(url).strip()
    if not text.lower().startswith(("http://", "https://")):
        return text
    parts = urlsplit(text)
    path = quote(parts.path, safe="/:@")
    query = quote(parts.query, safe="=&?/:;+") if parts.query else parts.query
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def _find_data_file(directory, stems):
    """在目录里按候选文件名找 stock/display（仅 .csv）。"""
    base = Path(directory)
    if not base.is_dir():
        return None
    for stem in stems:
        candidate = base / f"{stem}.csv"
        if candidate.is_file():
            return candidate
    return None


def _find_region_data_file(output_dir, stems):
    """在 Output 目录及其子目录（latest、时间戳文件夹）里找数据文件。"""
    base = Path(output_dir)
    if not base.is_dir():
        return None

    found = _find_data_file(base, stems)
    if found:
        return found

    latest = base / "latest"
    found = _find_data_file(latest, stems)
    if found:
        return found

    stamp_dirs = sorted(
        (
            d for d in base.iterdir()
            if d.is_dir() and re.fullmatch(r"\d{8}_\d{6}", d.name)
        ),
        reverse=True,
    )
    for sub in stamp_dirs:
        found = _find_data_file(sub, stems)
        if found:
            return found
    return None


def _region_discontinued_stems(region_key):
    rk = region_key.upper()
    return ["stock_discontinued", f"{rk}_stock_discontinued"]


def _region_stock_stems(region_key):
    rk = region_key.upper()
    return ["stock", f"{rk}_stock", "product_stock_price", f"{rk}_product_stock_price"]


def _region_display_stems(region_key):
    rk = region_key.upper()
    return ["display", "display_with_families", f"{rk}_display",
            "store_display", f"{rk}_store_display"]


def _region_storage_stems(region_key):
    rk = region_key.upper()
    return ["storage", "shop_storage", f"{rk}_storage", "store_storage"]


def _region_on_hold_stems(region_key):
    rk = region_key.upper()
    return ["on_hold", "onhold", f"{rk}_on_hold", "stock_on_hold"]


def _region_parts_stems(region_key):
    rk = region_key.upper()
    return ["parts", "spare_parts", f"{rk}_parts", "part_stock"]


STORAGE_QTY_KEYS = ["storageqty", "storage_qty", "qty", "quantity", "displayqty"]
ON_HOLD_QTY_KEYS = [
    "onholdqty", "on_hold_qty", "holdqty", "hold_qty", "quantity", "qty",
    "onholdquantity", "sum", "total", "amount",
]
ON_HOLD_STATUS_KEYS = ["stockonholdstatus", "onholdstatus", "holdstatus", "status"]
ON_HOLD_DATE_KEYS = [
    "onholddate", "on_hold_date", "holddate", "hold_since", "holdstart", "hold_start",
    "stockonholddate", "onholdsince", "onholdtime", "startdate", "createdon", "modifiedon",
    "冻结日期", "冻结时间", "holdtime",
]
ON_HOLD_DAYS_KEYS = [
    "holddays", "on_hold_days", "daysonhold", "hold_days", "dayonhold", "冻结天数",
]
ON_HOLD_ORDER_KEYS = [
    "orderno", "order_no", "ordercode", "order_code", "ordernumber", "order_number",
    "salesorder", "sales_order", "sonumber", "so_number", "orderid", "order_id",
    "documentno", "document_no", "salesorderno", "weborder", "web_order", "confirmationno",
    "订单号", "销售订单", "订单编号",
]
ON_HOLD_TICKET_KEYS = [
    "ticket", "ticketno", "ticket_no", "ticketnumber", "ticket_number", "ticketid",
    "ticket_id", "serviceticket", "service_ticket", "caseno", "case_no", "工单号",
    "工单", "ticketref", "notes",
]
ON_HOLD_STOCK_ID_KEYS = ["stockid", "stock_id", "lineid", "line_id", "inventoryid"]
ON_HOLD_ANALYSIS_MAX_ROWS = 15000
PARTS_QTY_KEYS = [
    "partsqty", "parts_qty", "partqty", "quantity", "qty", "sum", "total", "amount",
]
MINING_CODE_KEYS = CODE_KEYS + [
    "serialnumber", "serial_number", "serial", "materialcode", "material", "barcode",
]


def _load_runner_regions():
    """合并默认三国配置与 region_runner_config(.local).json。"""
    try:
        from runner_config import load_runner_config
        file_regions = load_runner_config().get("regions", {})
        file_regions = {
            str(k).strip().upper(): v for k, v in file_regions.items() if isinstance(v, dict)
        }
    except Exception:
        file_regions = {}

    merged = {}
    for key in REGION_ORDER:
        base = dict(DEFAULT_REGION_META[key])
        if key in file_regions:
            base.update(file_regions[key])
        merged[key] = base
    for key, cfg in file_regions.items():
        if key not in merged:
            merged[key] = dict(cfg)
    return merged


def _region_output_dir(region_key):
    regions = _load_runner_regions()
    cfg = regions.get(region_key.upper(), {})
    output_dir = (cfg.get("output_dir") or f"Output-{region_key.upper()}").strip()
    path = Path(output_dir)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def resolve_sources(region=None):
    stock_env = os.getenv("INSTOCK_STOCK_CSV")
    display_env = os.getenv("INSTOCK_DISPLAY_CSV")
    data_dir = os.getenv("INSTOCK_DATA_DIR")
    region_key = (region or os.getenv("INSTOCK_REGION") or "").strip().upper()

    if stock_env and display_env:
        stock_path = Path(stock_env)
        return stock_path, Path(display_env), "csv", stock_path.parent
    if data_dir:
        d = Path(data_dir)
        return d / "stock.csv", d / "display.csv", "csv", d
    if region_key:
        d = _region_output_dir(region_key)
        stock_path = _find_region_data_file(d, _region_stock_stems(region_key))
        display_path = _find_region_data_file(d, _region_display_stems(region_key))
        if stock_path is None:
            stock_path = d / "stock.csv"
        if display_path is None:
            display_path = d / "display.csv"
        return stock_path, display_path, f"region-{region_key}", d
    raise ValueError(
        "未指定地区。请在界面选择 NZ/AU/CA，或设置环境变量 INSTOCK_REGION。"
    )


def list_regions():
    regions = _load_runner_regions()
    order = list(REGION_ORDER) + [k for k in regions if k not in REGION_ORDER]
    options = []
    for key in order:
        cfg = regions.get(key)
        if not cfg:
            continue
        out_dir = _region_output_dir(key)
        has_data = _find_region_data_file(out_dir, _region_stock_stems(key)) is not None
        options.append({
            "key": key,
            "label": cfg.get("label", key),
            "has_latest": has_data,
        })
    return options


def default_region():
    """返回第一个已有导出数据的地区，否则 NZ。"""
    regions = list_regions()
    for r in regions:
        if r.get("has_latest"):
            return r["key"]
    return regions[0]["key"] if regions else "NZ"


def clear_region_cache(region=None):
    if region is None:
        _REGION_CACHE.clear()
        _STORE_VIEW_CACHE.clear()
        return
    region_key = str(region).strip().upper()
    _REGION_CACHE.pop(region_key, None)
    for key in list(_STORE_VIEW_CACHE):
        if key[0] == region_key:
            _STORE_VIEW_CACHE.pop(key, None)


def _load_blacklist(path):
    """读取黑名单 SKU 集合（列名 sku / 编码 / ProductCode 等均可）。"""
    codes = set()
    if not path or not Path(path).is_file():
        return codes
    for row in _read_blacklist_table(path):
        code = _pick(row, BLACKLIST_KEYS)
        if code:
            codes.add(_norm_code(code))
    return codes


def _read_xlsx_rows(path):
    if _load_workbook is None:
        raise RuntimeError(
            f"黑名单为 Excel（{path.name}），请另存为 blacklist.csv，或运行：pip install openpyxl"
        )
    wb = _load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers = next(rows_iter, None)
    if not headers:
        wb.close()
        return []
    columns = [str(h).strip() if h is not None else "" for h in headers]
    out = []
    for row in rows_iter:
        if row is None:
            continue
        item = {}
        empty = True
        for idx, col in enumerate(columns):
            if not col:
                continue
            value = row[idx] if idx < len(row) else None
            if value is not None and str(value).strip() != "":
                empty = False
            item[col] = value
        if not empty:
            out.append(item)
    wb.close()
    return out


def _read_blacklist_table(path):
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        return _read_xlsx_rows(path)
    return _read_csv(path)


def _find_blacklist_file(data_dir):
    """黑名单支持 blacklist.csv / blacklist.xlsx（Excel 直接保存也可用）。"""
    base = Path(data_dir)
    if not base.is_dir():
        return None
    for stem in BLACKLIST_STEMS:
        for ext in (".csv", ".xlsx"):
            candidate = base / f"{stem}{ext}"
            if candidate.is_file():
                return candidate
    return _find_region_data_file(data_dir, BLACKLIST_STEMS)


def _resolve_blacklist_path(data_dir):
    return _find_blacklist_file(data_dir)


def expected_blacklist_path(region=None):
    """黑名单默认放置路径（Output-{region}/blacklist.csv）。"""
    region_key = str(region or default_region() or "NZ").strip().upper()
    return _region_output_dir(region_key) / "blacklist.csv"


def expected_channel_owner_path(region=None):
    """渠道负责人配置默认路径（Output-{region}/channel_owners.csv）。"""
    region_key = str(region or default_region() or "NZ").strip().upper()
    return _region_output_dir(region_key) / "channel_owners.csv"


def _find_channel_owner_file(data_dir):
    found = _find_region_data_file(data_dir, CHANNEL_OWNER_STEMS)
    if found:
        return found
    base = Path(data_dir)
    if not base.is_dir():
        return None
    for stem in CHANNEL_OWNER_STEMS:
        for sub in (base, base / "latest"):
            if not sub.is_dir():
                continue
            for ext in (".csv", ".xlsx"):
                candidate = sub / f"{stem}{ext}"
                if candidate.is_file():
                    return candidate
    return None


def _channel_owner_row_dict(owner, channel, extra=None):
    data = {
        "owner": str(owner or "").strip(),
        "channel": str(channel or "").strip(),
        "sub_channel": "",
        "lead_time": None,
        "merge_products": "所有",
        "merge_regions": "",
        "note": "",
    }
    if extra:
        data.update(extra)
    return data


def _parse_channel_owner_dict_row(row):
    owner = _strip_cell_bom(_pick(row, OWNER_KEYS) or "")
    channel = _normalize_channel_code(_pick(row, CHANNEL_KEYS) or "")
    if not owner or not channel:
        return None
    lead_raw = _pick(row, LEAD_TIME_KEYS)
    lead_time = None
    if lead_raw is not None and str(lead_raw).strip() != "":
        try:
            lead_time = int(float(str(lead_raw).strip()))
        except ValueError:
            lead_time = str(lead_raw).strip()
    return _channel_owner_row_dict(owner, channel, {
        "sub_channel": _normalize_channel_code(_pick(row, SUB_CHANNEL_KEYS) or ""),
        "lead_time": lead_time,
        "merge_products": str(_pick(row, MERGE_PRODUCT_KEYS) or "所有").strip() or "所有",
        "merge_regions": str(_pick(row, MERGE_REGION_KEYS) or "").strip(),
        "note": str(_pick(row, OWNER_NOTE_KEYS) or "").strip(),
    })


def _dict_row_has_channel_headers(row):
    keys = {str(k).strip().lower() for k in row.keys()}
    return bool(keys & {k.lower() for k in OWNER_KEYS}) and bool(keys & {k.lower() for k in CHANNEL_KEYS})


_CHANNEL_OWNER_ENCODINGS = (
    "utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be",
    "gbk", "gb2312", "cp936",
)


def _read_channel_owner_text(path):
    path = Path(path)
    for enc in _CHANNEL_OWNER_ENCODINGS:
        try:
            return path.read_text(encoding=enc), enc
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace"), "utf-8"


def _detect_channel_owner_delimiter(sample):
    counts = {"\t": sample.count("\t"), ";": sample.count(";"), ",": sample.count(",")}
    best = max(counts, key=lambda ch: counts[ch])
    return best if counts[best] > 0 else ","


def _normalize_channel_code(channel):
    text = _strip_cell_bom(channel)
    if re.fullmatch(r"\d+\.0+", text):
        try:
            return str(int(float(text)))
        except ValueError:
            pass
    return text


def _read_channel_owner_raw_lines(path):
    """Excel 另存 CSV 可能是 GBK、UTF-16、分号或 Tab 分隔，需兼容。"""
    raw_text, _ = _read_channel_owner_text(path)
    delimiter = _detect_channel_owner_delimiter(raw_text[:4096])
    return list(csv.reader(raw_text.splitlines(), delimiter=delimiter))


def _read_channel_owner_dict_rows(path):
    raw_text, _ = _read_channel_owner_text(path)
    delimiter = _detect_channel_owner_delimiter(raw_text[:4096])
    rows = []
    for row in csv.DictReader(raw_text.splitlines(), delimiter=delimiter):
        rows.append({_strip_cell_bom(k): v for k, v in row.items() if k is not None})
    return rows


def _load_channel_owner_config(path):
    rows = []
    if not path or not Path(path).is_file():
        return rows

    suffix = Path(path).suffix.lower()
    if suffix == ".xlsx":
        if _load_workbook is None:
            return rows
        wb = _load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        raw = []
        for row in ws.iter_rows(values_only=True):
            raw.append(["" if cell is None else cell for cell in row])
        wb.close()
    else:
        raw = _read_channel_owner_raw_lines(path)

    if not raw:
        return rows

    start = 0
    if len(raw[0]) >= 2:
        h0 = _strip_cell_bom(raw[0][0]).lower()
        h1 = _strip_cell_bom(raw[0][1]).lower()
        if (h0, h1) in CHANNEL_OWNER_HEADER_PAIRS or (
            h0 in {k.lower() for k in OWNER_KEYS} and h1 in {k.lower() for k in CHANNEL_KEYS}
        ):
            start = 1

    if start == 0:
        dict_rows = _read_channel_owner_dict_rows(path) if suffix != ".xlsx" else []
        if dict_rows and _dict_row_has_channel_headers(dict_rows[0]):
            for row in dict_rows:
                parsed = _parse_channel_owner_dict_row(row)
                if parsed:
                    rows.append(parsed)
            if rows:
                return rows

    owner_keys_lower = {k.lower() for k in OWNER_KEYS}
    channel_keys_lower = {k.lower() for k in CHANNEL_KEYS}
    for line in raw[start:]:
        if len(line) < 2:
            continue
        owner = _strip_cell_bom(line[0])
        channel = _normalize_channel_code(line[1])
        if not owner or not channel:
            continue
        if owner.lower() in owner_keys_lower and channel.lower() in channel_keys_lower:
            continue
        lead_time = None
        if len(line) > 2 and str(line[2]).strip():
            try:
                lead_time = int(float(str(line[2]).strip()))
            except ValueError:
                lead_time = str(line[2]).strip()
        rows.append(_channel_owner_row_dict(owner, channel, {
            "lead_time": lead_time,
            "merge_products": str(line[3]).strip() if len(line) > 3 and str(line[3]).strip() else "所有",
            "merge_regions": str(line[4]).strip() if len(line) > 4 else "",
            "note": str(line[5]).strip() if len(line) > 5 else "",
        }))
    return rows


def resolve_channel_owner_path(region=None, data_dir=None):
    """返回 Output 目录里 channel_owner(s).csv 的路径（不读内容）。"""
    region_key = str(region or default_region() or "NZ").strip().upper()
    base = Path(data_dir) if data_dir else _region_output_dir(region_key)
    path = _find_channel_owner_file(base)
    if path:
        return str(path)
    for stem in CHANNEL_OWNER_STEMS:
        for ext in (".csv", ".xlsx"):
            candidate = base / f"{stem}{ext}"
            if candidate.is_file():
                return str(candidate)
    return None


def load_channel_owner_config(region=None, data_dir=None):
    region_key = str(region or default_region() or "NZ").strip().upper()
    path = resolve_channel_owner_path(region, data_dir)
    if not path:
        template = ROOT_DIR / f"Data-{region_key}" / "channel_owners.example.csv"
        if template.is_file():
            path = str(template)
    rows = []
    if path:
        try:
            rows = _load_channel_owner_config(path)
        except Exception:
            rows = []
    return rows, path


def _prefix_number(code):
    text = sku_prefix(code)
    return int(text) if text.isdigit() else None


def product_matches_channel(code, channel_rule):
    """渠道列支持 271、155-319（前三位数值区间）等。"""
    rule = str(channel_rule or "").strip()
    if not rule:
        return False
    prefix = sku_prefix(code)
    if re.fullmatch(r"\d+-\d+", rule):
        lo_s, hi_s = rule.split("-", 1)
        pnum = _prefix_number(code)
        if pnum is not None:
            return int(lo_s) <= pnum <= int(hi_s)
    if prefix == rule[:3] or prefix == rule:
        return True
    return code.upper().startswith(rule.upper())


def _token_in_product(token, product):
    needle = str(token or "").strip().lower()
    if not needle:
        return False
    for field in (product.get("family"), product.get("name")):
        if field and needle in str(field).lower():
            return True
    return False


def _is_merge_all(merge_products):
    return str(merge_products or "所有").strip() in MERGE_ALL_TOKENS


def _compute_bucket_stats(products, merge_products, store_specific):
    """按渠道行配置计算统计单位；合并组=每个 token 至少有一款有货才算 1 个有货单位。"""
    if not products:
        return 0, 0, 0, 0
    if _is_merge_all(merge_products):
        total = len(products)
        in_stock = sum(1 for p in products if p.get("in_stock"))
        gap = sum(1 for p in products if p.get("gap")) if store_specific else 0
        exempted = sum(1 for p in products if p.get("exempted")) if store_specific else 0
        return total, in_stock, gap, exempted

    tokens = [t.strip() for t in str(merge_products).split("+") if t.strip()]
    matched = [p for p in products if any(_token_in_product(t, p) for t in tokens)]
    remaining = [p for p in products if p not in matched]

    total_units = in_stock_units = gap_units = exempted_units = 0
    if tokens and matched:
        total_units += 1
        group_in_stock = all(
            any(_token_in_product(t, p) and p.get("in_stock") for p in matched)
            for t in tokens
        )
        if group_in_stock:
            in_stock_units += 1
        if store_specific:
            if any(p.get("gap") for p in matched):
                gap_units += 1
            if any(p.get("exempted") for p in matched):
                exempted_units += 1

    for p in remaining:
        total_units += 1
        if p.get("in_stock"):
            in_stock_units += 1
        if store_specific and p.get("gap"):
            gap_units += 1
        if store_specific and p.get("exempted"):
            exempted_units += 1
    return total_units, in_stock_units, gap_units, exempted_units


def _owner_for_prefix(prefix, config_rows):
    for row in config_rows:
        if product_matches_channel(f"{prefix}-000", row["channel"]):
            return row["owner"]
    return ""


def aggregate_by_channel_owner(products, region=None, store_specific=True, config_rows=None):
    """按 channel_owners.csv 生成负责人汇总 + 渠道明细。"""
    if config_rows is None:
        config_rows, _ = load_channel_owner_config(region)
    if not config_rows:
        return {"owners": [], "channels": [], "config_rows": 0}

    active = [p for p in products if not p.get("discontinued")]
    channel_rows = []
    for cfg in config_rows:
        matched = [p for p in active if product_matches_channel(p.get("code", ""), cfg["channel"])]
        if not matched:
            continue
        if not _is_merge_all(cfg["merge_products"]):
            tokens = [t.strip() for t in cfg["merge_products"].split("+") if t.strip()]
            matched = [p for p in matched if any(_token_in_product(t, p) for t in tokens)]
            if not matched:
                continue
        total, in_stock, gap, exempted = _compute_bucket_stats(
            matched, cfg["merge_products"], store_specific,
        )
        channel_rows.append({
            "owner": cfg["owner"],
            "channel": cfg["channel"],
            "lead_time": cfg.get("lead_time"),
            "merge_products": cfg["merge_products"],
            "merge_regions": cfg.get("merge_regions") or "",
            "note": cfg.get("note") or "",
            "sku_count": len(matched),
            "total_units": total,
            "in_stock_units": in_stock,
            "in_stock_rate": round(in_stock / total * 100, 1) if total else None,
            "gap_count": gap if store_specific else None,
            "exempted_count": exempted if store_specific else None,
        })

    channel_rows.sort(key=lambda r: (r["owner"].lower(), -(r["in_stock_rate"] or 0), r["channel"]))

    owner_buckets = {}
    for row in channel_rows:
        bucket = owner_buckets.setdefault(row["owner"], {
            "owner": row["owner"],
            "channel_count": 0,
            "sku_count": 0,
            "total_units": 0,
            "in_stock_units": 0,
            "gap_count": 0,
            "exempted_count": 0,
        })
        bucket["channel_count"] += 1
        bucket["sku_count"] += row["sku_count"]
        bucket["total_units"] += row["total_units"]
        bucket["in_stock_units"] += row["in_stock_units"]
        if store_specific:
            bucket["gap_count"] += row.get("gap_count") or 0
            bucket["exempted_count"] += row.get("exempted_count") or 0

    owner_rows = []
    for owner, bucket in owner_buckets.items():
        total = bucket["total_units"]
        in_stock = bucket["in_stock_units"]
        owner_rows.append({
            "owner": owner,
            "channel_count": bucket["channel_count"],
            "sku_count": bucket["sku_count"],
            "total_units": total,
            "in_stock_units": in_stock,
            "in_stock_rate": round(in_stock / total * 100, 1) if total else None,
            "gap_count": bucket["gap_count"] if store_specific else None,
            "exempted_count": bucket["exempted_count"] if store_specific else None,
        })
    owner_rows.sort(key=lambda r: (-(r["in_stock_rate"] or 0), r["owner"].lower()))
    return {
        "owners": owner_rows,
        "channels": channel_rows,
        "config_rows": len(config_rows),
    }


def _ensure_discontinued_rows(bundle):
    """按需解析停产 SKU；优先读独立的 stock_discontinued.csv（比从大 stock 里筛快）。"""
    if bundle.get("discontinued_loaded"):
        return
    data_dir = bundle.get("data_dir")
    disc_path = bundle.get("stock_discontinued_path")
    if disc_path and Path(disc_path).is_file():
        disc = _load_stock(_read_table(disc_path), data_dir, discontinued=None)
        for p in disc:
            p["discontinued"] = True
    else:
        raw = bundle.get("stock_raw_rows")
        if not raw:
            bundle["discontinued_loaded"] = True
            return
        disc = _load_stock(raw, data_dir, discontinued=True)
    bundle["stock_rows_discontinued"] = disc
    bundle["stock_rows"] = list(bundle["stock_rows_active"]) + disc
    _enrich_catalog_metadata(disc)
    bundle["discontinued_loaded"] = True


def _load_region_bundle(region, force=False):
    """读取并缓存某地区的 stock/display 原始表（切换店面时复用，避免重复读 CSV）。"""
    region_key = str(region).strip().upper()
    stock_path, display_path, source, data_dir = resolve_sources(region_key)
    stock_mtime = _file_mtime(stock_path)
    display_mtime = _file_mtime(display_path)
    blacklist_path = _resolve_blacklist_path(data_dir)
    blacklist_mtime = _file_mtime(blacklist_path) if blacklist_path else None
    disc_path = _find_region_data_file(data_dir, _region_discontinued_stems(region_key))
    disc_mtime = _file_mtime(disc_path) if disc_path else None
    storage_path = _find_region_data_file(data_dir, _region_storage_stems(region_key))
    storage_mtime = _file_mtime(storage_path) if storage_path else None
    on_hold_path = _find_region_data_file(data_dir, _region_on_hold_stems(region_key))
    on_hold_mtime = _file_mtime(on_hold_path) if on_hold_path else None
    parts_path = _find_region_data_file(data_dir, _region_parts_stems(region_key))
    parts_mtime = _file_mtime(parts_path) if parts_path else None

    cached = _REGION_CACHE.get(region_key)
    if (
        not force
        and cached
        and cached["stock_mtime"] == stock_mtime
        and cached["display_mtime"] == display_mtime
        and cached.get("blacklist_mtime") == blacklist_mtime
        and cached.get("stock_discontinued_mtime") == disc_mtime
        and cached.get("storage_mtime") == storage_mtime
        and cached.get("on_hold_mtime") == on_hold_mtime
        and cached.get("parts_mtime") == parts_mtime
    ):
        return cached

    if not Path(stock_path).is_file():
        raise FileNotFoundError(stock_path)
    if not Path(display_path).is_file():
        raise FileNotFoundError(display_path)

    display_rows = _read_table(display_path)
    by_store, display_details = _load_display(display_rows)
    by_storage, storage_details = {}, {}
    storage_row_count = 0
    if storage_path and Path(storage_path).is_file():
        storage_rows = _read_table(storage_path)
        by_storage, storage_details, storage_map, storage_unmapped = _load_storage(
            storage_rows, by_store.keys(),
        )
        storage_row_count = len(storage_rows)
    else:
        storage_map, storage_unmapped = {}, []
    on_hold_by_code = {}
    on_hold_row_count = 0
    if on_hold_path and Path(on_hold_path).is_file():
        on_hold_rows = _read_mining_table(on_hold_path)
        on_hold_detail_rows = _parse_on_hold_detail_rows(on_hold_rows)
        on_hold_by_code = _load_on_hold_inventory(on_hold_rows)
        on_hold_row_count = len(on_hold_rows)
    else:
        on_hold_rows = []
        on_hold_detail_rows = []
    parts_by_code = {}
    parts_row_count = 0
    if parts_path and Path(parts_path).is_file():
        parts_rows = _read_mining_table(parts_path)
        parts_by_code = _load_parts_inventory_wide(parts_rows) or _load_parts_inventory(parts_rows)
        parts_row_count = len(parts_rows)
        parts_kit_bom = _load_parts_kit_bom(data_dir)
        parts_detail_rows = _parse_parts_detail_rows(parts_rows)
        parts_detail_rows = _apply_parts_kit_bom(parts_detail_rows, parts_kit_bom)
        parts_detail_rows = _reparent_parts_detail(parts_detail_rows)
    else:
        parts_rows = []
        parts_detail_rows = []
        parts_kit_bom = {}
    blacklist = _load_blacklist(blacklist_path)
    stock_raw_rows = _read_table(stock_path)
    warehouse_transfer_hints = _warehouse_hints_from_stock_rows(stock_raw_rows)
    warehouse_bucket_overrides = _load_warehouse_bucket_overrides(data_dir)
    parts_detail_rows = _apply_warehouse_buckets(
        parts_detail_rows, warehouse_transfer_hints, warehouse_bucket_overrides,
    )
    active_rows = _load_stock(stock_raw_rows, data_dir, discontinued=False)
    _enrich_catalog_metadata(active_rows)
    bundle = {
        "region": region_key,
        "stock_path": stock_path,
        "display_path": display_path,
        "storage_path": str(storage_path) if storage_path else None,
        "storage_mtime": storage_mtime,
        "blacklist_path": str(blacklist_path) if blacklist_path else None,
        "blacklist": blacklist,
        "source": source,
        "data_dir": data_dir,
        "stock_mtime": stock_mtime,
        "display_mtime": display_mtime,
        "blacklist_mtime": blacklist_mtime,
        "stock_discontinued_path": str(disc_path) if disc_path else None,
        "stock_discontinued_mtime": disc_mtime,
        "stock_raw_rows": stock_raw_rows,
        "stock_rows": active_rows,
        "stock_rows_active": active_rows,
        "stock_rows_discontinued": [],
        "discontinued_loaded": False,
        "by_store": by_store,
        "display_details": display_details,
        "by_storage": by_storage,
        "storage_details": storage_details,
        "storage_map": storage_map,
        "storage_unmapped": storage_unmapped,
        "display_row_count": len(display_rows),
        "storage_row_count": storage_row_count,
        "on_hold_path": str(on_hold_path) if on_hold_path else None,
        "on_hold_mtime": on_hold_mtime,
        "on_hold_by_code": on_hold_by_code,
        "on_hold_rows": on_hold_rows,
        "on_hold_detail_rows": on_hold_detail_rows,
        "on_hold_row_count": on_hold_row_count,
        "parts_path": str(parts_path) if parts_path else None,
        "parts_mtime": parts_mtime,
        "parts_by_code": parts_by_code,
        "parts_row_count": parts_row_count,
        "parts_detail_rows": parts_detail_rows,
        "parts_rows": parts_rows,
        "parts_kit_bom": parts_kit_bom if parts_path and Path(parts_path).is_file() else {},
        "warehouse_transfer_hints": warehouse_transfer_hints,
        "warehouse_bucket_overrides": warehouse_bucket_overrides,
        "stock_row_count": len(stock_raw_rows),
    }
    _REGION_CACHE[region_key] = bundle
    if EAGER_DISCONTINUED_STOCK:
        _ensure_discontinued_rows(bundle)
        bundle.pop("stock_raw_rows", None)
    return bundle


def _count_image_urls(products):
    count = 0
    for product in products:
        raw = product.get("image_raw")
        if raw and str(raw).strip():
            count += 1
        elif product.get("image"):
            count += 1
    return count


def _stock_files_label(bundle):
    active = Path(bundle.get("stock_path") or "stock.csv").name
    disc_path = bundle.get("stock_discontinued_path")
    if disc_path and Path(disc_path).is_file():
        return f"{active} + {Path(disc_path).name}（两库）"
    return active


def _panel_data_files_label(bundle):
    """看板顶部「数据源」行：库存 + 陈列 + 店后仓。"""
    parts = [_stock_files_label(bundle)]
    display_path = bundle.get("display_path")
    if display_path and Path(display_path).is_file():
        parts.append(Path(display_path).name)
    storage_path = bundle.get("storage_path")
    if storage_path and Path(storage_path).is_file():
        parts.append(f"{Path(storage_path).name}（店后仓）")
    on_hold_path = bundle.get("on_hold_path")
    if on_hold_path and Path(on_hold_path).is_file():
        parts.append(Path(on_hold_path).name)
    parts_path = bundle.get("parts_path")
    if parts_path and Path(parts_path).is_file():
        parts.append(Path(parts_path).name)
    return " + ".join(parts)


def get_region_bundle(region=None, force=False):
    """供看板读取 on_hold / parts 等扩展数据。"""
    region_key = str(region or default_region() or "NZ").strip().upper()
    return _load_region_bundle(region_key, force=force)


def _region_from_data_dir(data_dir, region=None):
    if region:
        return str(region).strip().upper()
    env = os.getenv("INSTOCK_REGION", "").strip().upper()
    if env:
        return env
    text = str(data_dir or "").replace("\\", "/").upper()
    for rk in REGION_ORDER:
        if f"OUTPUT-{rk}" in text:
            return rk
    return "NZ"


def _image_base_url(region_key):
    env = os.getenv("INSTOCK_IMAGE_BASE", "").strip()
    if env:
        return env if env.endswith("/") else env + "/"
    base = REGION_IMAGE_BASE.get(str(region_key or "").upper(), "")
    return base if base.endswith("/") else (base + "/" if base else "")


def image_url_candidates(url):
    """同一图片路径尝试多个域名（ierpapi 失效时回退官网 CDN）。"""
    text = _clean_image_ref(url)
    if not text or not text.lower().startswith(("http://", "https://")):
        return [text] if text else []
    seen = {text}
    out = [text]
    for host, alternates in IMAGE_HOST_ALTERNATES.items():
        if host in text.lower():
            for alt in alternates:
                candidate = re.sub(host, alt, text, flags=re.I)
                if candidate not in seen:
                    seen.add(candidate)
                    out.append(candidate)
    return out


def _clean_image_ref(value):
    text = str(value).strip().replace("\\", "/")
    if not text:
        return ""
    if "://" in text:
        parts = urlsplit(text)
        path = re.sub(r"/+", "/", parts.path or "/")
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
    return text


def _resolve_image(raw, code, data_dir, scan_dir=True, region=None):
    """把图片列的值解析成可用路径/URL；也支持按产品编码自动找本地图。"""
    region_key = _region_from_data_dir(data_dir, region)
    base_url = _image_base_url(region_key)

    if raw:
        text = _clean_image_ref(raw)
        if text.lower().startswith(("http://", "https://")):
            return normalize_url(text)
        p = Path(text)
        candidates = []
        if p.is_absolute():
            candidates.append(p)
        else:
            if data_dir:
                candidates.append(Path(data_dir) / text.lstrip("/"))
            candidates.append(ROOT_DIR / text.lstrip("/"))
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        if base_url and text:
            return normalize_url(base_url.rstrip("/") + "/" + text.lstrip("/"))

    if not scan_dir:
        return None

    search_dirs = []
    if data_dir:
        search_dirs.append(Path(data_dir) / "images")
    search_dirs.append(ROOT_DIR / "sample_images")
    for images_dir in search_dirs:
        if not images_dir.is_dir():
            continue
        for stem in (str(code).strip(), str(code).strip().upper(), str(code).strip().lower()):
            for ext in IMAGE_EXTS:
                candidate = images_dir / f"{stem}{ext}"
                if candidate.is_file():
                    return str(candidate)
    return None


def resolve_product_image(product, data_dir, region=None):
    """按需解析产品图（避免启动时对上万 SKU 扫描 images 目录）。"""
    if product.get("_image_resolved"):
        return product.get("image")
    raw = product.get("image_raw")
    image = _resolve_image(
        raw, product.get("code"), data_dir, scan_dir=True,
        region=region or product.get("region"),
    )
    product["image"] = image
    product["_image_resolved"] = True
    return image


def _load_stock(rows, data_dir, discontinued=None):
    """discontinued: None=全部，False=仅在产，True=仅停产。"""
    out = []
    for row in rows:
        code = _pick(row, CODE_KEYS)
        if not code:
            continue
        is_disc = _is_discontinued(_pick(row, DISCONTINUE_KEYS))
        if discontinued is False and is_disc:
            continue
        if discontinued is True and not is_disc:
            continue
        warehouse_stock = _extract_warehouse_stock(row)
        if warehouse_stock:
            qty = sum(warehouse_stock.values())
        else:
            qty = _to_float(_pick(row, STOCK_KEYS)) or 0.0
        image_raw = _pick(row, IMAGE_KEYS)
        pre_image = None
        if image_raw and str(image_raw).strip().lower().startswith(("http://", "https://")):
            pre_image = _resolve_image(image_raw, code, data_dir, scan_dir=False)
        out.append({
            "code": str(code).strip(),
            "norm_code": _norm_code(code),
            "name": str(_pick(row, NAME_KEYS) or "").strip(),
            "family": str(_pick(row, FAMILY_KEYS) or "未分类").strip(),
            "stock_qty": qty,
            "warehouse_stock": warehouse_stock,
            "price": _to_float(_pick(row, PRICE_KEYS)),
            "discontinued": is_disc,
            "in_stock": qty > 0,
            "image_raw": image_raw,
            "image": pre_image,
            "_image_resolved": bool(pre_image),
        })
    return out


def _storage_warehouse_to_display_store(warehouse_name):
    """Onehunga Shop-Storage → Onehunga Shop-Display（与 display.csv 店面下拉对齐）。"""
    text = str(warehouse_name or "").strip()
    if not text:
        return text
    for old, new in (
        ("-Storage", "-Display"),
        (" Storage", " Display"),
        ("-storage", "-Display"),
        (" storage", " display"),
    ):
        if old in text:
            return text.replace(old, new, 1)
    lower = text.lower()
    if lower.endswith("storage"):
        base = text[: -len("storage")].rstrip(" -")
        if base.lower().endswith("shop"):
            return f"{base}-Display"
        return f"{base} Display" if base else text
    return text


def _storage_location_tokens(warehouse_name):
    """从 Storage 仓名提取地点 token，用于与 display 店面名模糊对齐。"""
    text = re.sub(r"\bstorage\b", " ", str(warehouse_name or ""), flags=re.I)
    text = re.sub(r"\bshop\b", " ", text, flags=re.I)
    return set(_store_name_tokens(text))


def _display_store_match_score(warehouse_name, store_name):
    """Storage 仓名与陈列店面名的匹配分；分越高越优先。"""
    wh_tokens = _storage_location_tokens(warehouse_name)
    st_tokens = set(_store_name_tokens(store_name))
    score = float(len(wh_tokens & st_tokens))
    text = str(store_name).lower()
    if "shop" in str(warehouse_name).lower() and "shop" in text:
        score += 0.25
    if any(x in text for x in ("no longer", "old display", "repair", "outlet", "(no ")):
        score -= 1.0
    return score


def _resolve_storage_display_store(warehouse_name, display_store_keys):
    """把 storage.csv 的 WarehouseName 对齐到 display.csv 里的店面下拉名。"""
    candidate = _storage_warehouse_to_display_store(warehouse_name)
    stores = [s for s in (display_store_keys or []) if s and s != ALL_STORES]
    if not stores:
        return candidate, None

    for store in stores:
        if store == candidate or store.lower() == candidate.lower():
            return store, str(warehouse_name).strip() or None

    wh_tokens = _storage_location_tokens(warehouse_name)
    if wh_tokens:
        best_store = None
        best_score = 0.0
        for store in stores:
            score = _display_store_match_score(warehouse_name, store)
            if score > best_score:
                best_score = score
                best_store = store
        if best_score > 0:
            return best_store, str(warehouse_name).strip() or None

    return candidate, str(warehouse_name).strip() or None


def _load_storage(rows, display_store_keys=()):
    """返回 (by_store, storage_details, storage_map, unmapped_warehouses)。"""
    by_store = {}
    storage_details = {}
    storage_map = {}
    unmapped = []
    for row in rows:
        code = _pick(row, CODE_KEYS)
        if not code:
            continue
        warehouse = _pick(row, STORE_KEYS + ["warehousename"])
        store, raw_wh = _resolve_storage_display_store(warehouse, display_store_keys)
        store = str(store).strip() if store else "（未标注店面）"
        if raw_wh and store not in display_store_keys:
            if raw_wh not in unmapped:
                unmapped.append(raw_wh)
        elif raw_wh:
            storage_map.setdefault(store, raw_wh)
        qty = _to_float(_pick(row, STORAGE_QTY_KEYS)) or 0.0
        if qty <= 0:
            continue
        norm = _norm_code(code)
        by_store.setdefault(store, set()).add(norm)
        prev = storage_details.setdefault(store, {}).get(norm)
        if prev:
            prev["qty"] = prev.get("qty", 0) + qty
        else:
            storage_details.setdefault(store, {})[norm] = {
                "code": str(code).strip(),
                "family": str(_pick(row, FAMILY_KEYS) or "").strip(),
                "name": str(_pick(row, NAME_KEYS) or "").strip(),
                "qty": qty,
            }
    return by_store, storage_details, storage_map, unmapped


def _column_key_map(row):
    return {
        re.sub(r"[\s_\-]+", "", _strip_cell_bom(k).lower()): v
        for k, v in row.items() if k is not None
    }


def _pick_fuzzy(row, keys):
    lower = _column_key_map(row)
    for key in keys:
        nk = re.sub(r"[\s_\-]+", "", str(key).lower())
        if nk in lower:
            val = lower[nk]
            if val is not None and str(val).strip() != "":
                return val
    return None


def _looks_like_product_sku(text):
    text = str(text or "").strip().upper()
    if not text or len(text) < 4:
        return False
    if re.fullmatch(r"\d{3}-\d{2,}([-.][A-Z0-9]+)?", text):
        return True
    if re.fullmatch(r"[A-Z0-9]{2,}-\d{2,}", text):
        return True
    return False


def _mining_code_from_row(row):
    code = _pick(row, CODE_KEYS) or _pick_fuzzy(row, MINING_CODE_KEYS)
    if code:
        return str(code).strip()
    for val in row.values():
        text = str(val or "").strip()
        if _looks_like_product_sku(text):
            return text
    return ""


def _mining_qty_from_row(row, qty_keys):
    """无数量列时按 1 计（序列号级导出每行=1 件）。"""
    raw = _pick(row, qty_keys) or _pick_fuzzy(row, qty_keys)
    qty = _to_float(raw)
    if qty is None or qty <= 0:
        return 1.0
    return qty


def _on_hold_qty_from_row(row):
    """On Hold 行级数量：Quantity=0 的行保留为 0（不当作 1），分析页会跳过。"""
    raw = _pick(row, ON_HOLD_QTY_KEYS) or _pick_fuzzy(row, ON_HOLD_QTY_KEYS)
    qty = _to_float(raw)
    if qty is None:
        return 1.0
    return max(0.0, qty)


def _normalize_on_hold_status(status):
    return re.sub(r"\s+", " ", str(status or "").strip())


def _pick_on_hold_order_no(row):
    val = _pick_fuzzy(row, ON_HOLD_ORDER_KEYS)
    if val is not None and str(val).strip():
        return str(val).strip()
    lower = _column_key_map(row)
    for col_norm, cell in lower.items():
        if "order" not in col_norm or "hold" in col_norm:
            continue
        text = str(cell or "").strip()
        if not text:
            continue
        if _looks_like_product_sku(text):
            continue
        return text
    return ""


def _pick_on_hold_ticket_no(row, order_no=""):
    val = _pick_fuzzy(row, ON_HOLD_TICKET_KEYS)
    if val is not None and str(val).strip():
        text = str(val).strip()
        if text.lower() not in ("notes", "note", "备注"):
            return text
    order_no = str(order_no or "").strip()
    if "." in order_no:
        suffix = order_no.rsplit(".", 1)[-1].strip()
        if suffix and len(suffix) <= 12:
            return suffix
    return ""


def _read_mining_table(path):
    """on_hold / parts 可能是 Excel 分号或 Tab 导出。"""
    path = Path(path)
    raw_text, _ = _read_channel_owner_text(path)
    delimiter = _detect_channel_owner_delimiter(raw_text[:8192])
    return list(csv.DictReader(raw_text.splitlines(), delimiter=delimiter))


def _parse_hold_datetime(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    serial = _to_float(text)
    if serial is not None and 20000 < serial < 80000:
        try:
            from datetime import timedelta
            base = date(1899, 12, 30)
            return base + timedelta(days=int(serial))
        except (ValueError, OverflowError):
            pass
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d+)?(\.\d+)?", text):
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y",
        "%Y/%m/%d", "%d-%m-%Y", "%Y%m%d",
    ):
        try:
            chunk = text[:19] if len(text) > 10 and " " in text else text[:10]
            return datetime.strptime(chunk, fmt).date()
        except ValueError:
            continue
    return None


def _hold_days_from_row(row, hold_at=None):
    raw = _pick_fuzzy(row, ON_HOLD_DAYS_KEYS)
    days = _to_float(raw)
    if days is not None and days >= 0:
        return int(days)
    if hold_at:
        return max(0, (date.today() - hold_at).days)
    return None


def _format_hold_since(hold_at):
    if not hold_at:
        return ""
    if isinstance(hold_at, datetime):
        hold_at = hold_at.date()
    return hold_at.isoformat()


def _on_hold_status_tokens(status):
    """一行里可能用逗号拼接多种 On Hold 类型（旧版汇总导出）；筛选时按子类型匹配。"""
    text = _normalize_on_hold_status(status)
    if not text:
        return ["（未标注状态）"]
    parts = [_normalize_on_hold_status(p) for p in re.split(r"[,;|/]", text) if p.strip()]
    return parts if parts else [text]


def _parse_on_hold_detail_rows(rows):
    """按 CSV 原始行保留 On Hold（不合并 SKU），每行含状态/数量/仓/订单或工单。"""
    detail = []
    for row in rows:
        code = _mining_code_from_row(row)
        if not code:
            continue
        status = _normalize_on_hold_status(
            _pick(row, ON_HOLD_STATUS_KEYS) or _pick_fuzzy(row, ON_HOLD_STATUS_KEYS) or ""
        ) or "（未标注状态）"
        warehouse = str(
            _pick(row, STORE_KEYS + ["warehousename"]) or _pick_fuzzy(row, STORE_KEYS + ["warehousename"]) or ""
        ).strip()
        order_no = _pick_on_hold_order_no(row)
        ticket_no = _pick_on_hold_ticket_no(row, order_no=order_no)
        hold_at = _parse_hold_datetime(_pick_fuzzy(row, ON_HOLD_DATE_KEYS))
        hold_days = _hold_days_from_row(row, hold_at)
        qty = _on_hold_qty_from_row(row)
        if qty <= 0:
            continue
        norm = _norm_code(code)
        detail.append({
            "norm_code": norm,
            "code": str(code).strip(),
            "name": str(_pick(row, NAME_KEYS) or _pick_fuzzy(row, NAME_KEYS) or "").strip(),
            "family": str(_pick(row, FAMILY_KEYS) or "").strip(),
            "status": status,
            "order_no": order_no,
            "ticket_no": ticket_no,
            "warehouse": warehouse,
            "qty": qty,
            "hold_at": hold_at,
            "hold_days": hold_days,
            "hold_since": _format_hold_since(hold_at),
        })
    return detail


def _load_on_hold_inventory(rows):
    """on_hold.csv → {norm_code: {code, name, family, total_qty, statuses, warehouses, ...}}"""
    by_code = {}
    for row in rows:
        code = _mining_code_from_row(row)
        if not code:
            continue
        norm = _norm_code(code)
        qty = _mining_qty_from_row(row, ON_HOLD_QTY_KEYS)
        status = str(_pick(row, ON_HOLD_STATUS_KEYS) or _pick_fuzzy(row, ON_HOLD_STATUS_KEYS) or "").strip()
        if not status:
            status = "（未标注状态）"
        warehouse = str(
            _pick(row, STORE_KEYS + ["warehousename"]) or _pick_fuzzy(row, STORE_KEYS + ["warehousename"]) or ""
        ).strip()
        hold_at = _parse_hold_datetime(_pick_fuzzy(row, ON_HOLD_DATE_KEYS))
        hold_days = _hold_days_from_row(row, hold_at)
        bucket = by_code.setdefault(norm, {
            "code": str(code).strip(),
            "name": str(_pick(row, NAME_KEYS) or _pick_fuzzy(row, NAME_KEYS) or "").strip(),
            "family": str(_pick(row, FAMILY_KEYS) or "").strip(),
            "total_qty": 0.0,
            "statuses": set(),
            "status_qty": {},
            "warehouses": [],
            "earliest_hold_at": None,
            "max_hold_days": None,
        })
        bucket["total_qty"] += qty
        bucket["statuses"].add(status)
        bucket["status_qty"][status] = bucket["status_qty"].get(status, 0.0) + qty
        if hold_at:
            prev = bucket.get("earliest_hold_at")
            if not prev or hold_at < prev:
                bucket["earliest_hold_at"] = hold_at
        if hold_days is not None:
            prev_days = bucket.get("max_hold_days")
            bucket["max_hold_days"] = max(prev_days if prev_days is not None else 0, hold_days)
        if warehouse:
            bucket["warehouses"].append({
                "warehouse": warehouse, "qty": qty, "status": status,
                "hold_at": hold_at, "hold_days": hold_days,
            })
    return by_code


def aggregate_on_hold_by_status(on_hold_rows=None, on_hold_by_code=None):
    """按 StockOnHoldStatus 汇总行数 / SKU 数 / 数量。"""
    by_status = defaultdict(lambda: {"row_count": 0, "sku_codes": set(), "total_qty": 0.0})
    if on_hold_rows:
        for row in on_hold_rows:
            code = _mining_code_from_row(row)
            if not code:
                continue
            status = _normalize_on_hold_status(
                _pick(row, ON_HOLD_STATUS_KEYS) or _pick_fuzzy(row, ON_HOLD_STATUS_KEYS) or ""
            ) or "（未标注状态）"
            qty = _on_hold_qty_from_row(row)
            if qty <= 0:
                continue
            slot = by_status[status]
            slot["row_count"] += 1
            slot["sku_codes"].add(_norm_code(code))
            slot["total_qty"] += qty
    elif on_hold_by_code:
        for item in on_hold_by_code.values():
            for status, qty in (item.get("status_qty") or {}).items():
                slot = by_status[status or "（未标注状态）"]
                slot["row_count"] += 1
                slot["sku_codes"].add(_norm_code(item.get("code")))
                slot["total_qty"] += float(qty or 0)
    rows = []
    for status, slot in by_status.items():
        rows.append({
            "status": status,
            "row_count": slot["row_count"],
            "sku_count": len(slot["sku_codes"]),
            "total_qty": int(slot["total_qty"]) if slot["total_qty"] == int(slot["total_qty"]) else slot["total_qty"],
        })
    rows.sort(key=lambda r: (-r["total_qty"], r["status"]))
    return rows


def list_on_hold_status_options(bundle):
    rows = aggregate_on_hold_by_status(
        on_hold_rows=bundle.get("on_hold_rows"),
        on_hold_by_code=bundle.get("on_hold_by_code"),
    )
    return [r["status"] for r in rows]


def diagnose_on_hold_bundle(bundle):
    """说明 On Hold 为 0 的常见原因（文件缺失 / 空文件 / 列名不匹配）。"""
    path = bundle.get("on_hold_path")
    row_count = int(bundle.get("on_hold_row_count") or 0)
    sku_count = len(bundle.get("on_hold_by_code") or {})
    if not path or not Path(path).is_file():
        return (
            "未找到 on_hold.csv：请在 Data-NZ 放置 on_hold.txt，执行 SQL 导出到 Output-NZ/on_hold.csv，"
            "然后点「刷新数据」。"
        )
    name = Path(path).name
    if row_count <= 0:
        return (
            f"已找到 {name}，但没有数据行（0 行）。请运行 on_hold.txt 导出；"
            "仅有表头或空文件时看板会显示 On Hold 0。"
        )
    if sku_count <= 0:
        sample = (bundle.get("on_hold_rows") or [])[:1]
        cols = list(sample[0].keys()) if sample else []
        col_text = "、".join(str(c) for c in cols[:10]) if cols else "（无表头）"
        return (
            f"已读取 {row_count} 行，但未识别 SKU（当前 0 个）。表头：{col_text}。"
            "请确认含 Sku / ProductCode 列，或 SKU 形如 130-051。"
        )
    return ""


def parse_on_hold_min_days(filter_label):
    """冻结天数筛选：全部 / 30天以上 / 90天以上 / 360天以上。"""
    text = str(filter_label or "").strip()
    if not text or text in ("全部", "全部天数", "不限"):
        return None
    if "360" in text:
        return 360
    if "90" in text:
        return 90
    if "30" in text:
        return 30
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def list_on_hold_analysis(
    bundle, status_filter=None, catalog_by_norm=None, max_rows=None, min_hold_days=None,
):
    """On Hold 明细：每行 CSV 一条（同 SKU 不同订单/时间分开），可按状态精确筛选。"""
    detail = list(bundle.get("on_hold_detail_rows") or [])
    if not detail and bundle.get("on_hold_rows"):
        detail = _parse_on_hold_detail_rows(bundle.get("on_hold_rows"))
    catalog_by_norm = catalog_by_norm or {}
    status_filter = str(status_filter or "").strip()
    out = []
    for line in detail:
        status_raw = line.get("status") or "（未标注状态）"
        tokens = _on_hold_status_tokens(status_raw)
        norm_filter = _normalize_on_hold_status(status_filter)
        norm_tokens = [_normalize_on_hold_status(t) for t in tokens]
        if norm_filter and norm_filter not in ("", "全部状态"):
            if norm_filter not in norm_tokens:
                continue
        hold_days = line.get("hold_days")
        if min_hold_days is not None and min_hold_days > 0:
            if hold_days is None or hold_days < min_hold_days:
                continue
        display_status = (
            status_filter
            if norm_filter and norm_filter not in ("", "全部状态") and norm_filter in norm_tokens
            else status_raw
        )
        norm = line.get("norm_code") or ""
        cat = catalog_by_norm.get(norm) or {}
        out.append({
            "norm_code": norm,
            "code": line.get("code") or "",
            "name": line.get("name") or cat.get("name") or "",
            "family": line.get("family") or cat.get("family") or "",
            "status": display_status,
            "order_no": line.get("order_no") or "",
            "ticket_no": line.get("ticket_no") or "",
            "hold_days": line.get("hold_days"),
            "hold_since": line.get("hold_since") or "",
            "hold_at": line.get("hold_at"),
            "qty": line.get("qty") or 0,
            "warehouse": line.get("warehouse") or "",
            "image_raw": cat.get("image_raw"),
            "image": cat.get("image"),
        })
    def _sort_hold_key(r):
        hold_at = r.get("hold_at")
        hold_ord = hold_at.toordinal() if hold_at else 0
        return (
            hold_ord,
            str(r.get("order_no") or r.get("ticket_no") or ""),
            str(r.get("code") or ""),
        )

    out.sort(key=_sort_hold_key, reverse=True)
    cap = max_rows if max_rows is not None else ON_HOLD_ANALYSIS_MAX_ROWS
    if cap and len(out) > cap:
        return out[:cap], len(out)
    return out, len(out)


def _load_parts_inventory(rows):
    """parts.csv → {norm_code: {code, name, family, total_qty, warehouses}}"""
    by_code = {}
    for row in rows:
        code = _mining_code_from_row(row)
        if not code:
            continue
        norm = _norm_code(code)
        qty = _mining_qty_from_row(row, PARTS_QTY_KEYS)
        warehouse = str(
            _pick(row, STORE_KEYS + ["warehousename"]) or _pick_fuzzy(row, STORE_KEYS + ["warehousename"]) or ""
        ).strip()
        bucket = by_code.setdefault(norm, {
            "code": str(code).strip(),
            "name": str(_pick(row, NAME_KEYS) or "").strip(),
            "family": str(_pick(row, FAMILY_KEYS) or "").strip(),
            "total_qty": 0.0,
            "warehouses": [],
        })
        bucket["total_qty"] += qty
        if warehouse:
            bucket["warehouses"].append({"warehouse": warehouse, "qty": qty})
    return by_code


def _infer_kit_parent_sku(sku):
    """从配件 SKU 推断母件，如 130-051-LEG → 130-051。"""
    text = str(sku or "").strip().upper()
    text = text.replace(".", "-").replace("_", "-")
    match = re.match(r"^(\d+-\d+)(?:-.+)?$", text)
    if match:
        return match.group(1)
    for pat in (
        r"^(.+)-(?:PART|PKT|PK|LEG|ARM|BASE|TOP|BTM|LHS|RHS|L|R|A|B|C|D)(?:\d*)$",
        r"^(.+)-(\d+)$",
    ):
        m = re.match(pat, text, re.I)
        if m and len(m.group(1)) >= 5:
            return m.group(1).upper()
    match = re.match(r"^(\d+-\d+)", text)
    if match:
        return match.group(1)
    return text


def _load_parts_kit_bom(data_dir):
    """可选 parts_kits.csv：parent_sku, part_sku, qty_per_set。"""
    if not data_dir:
        return {}
    path = _find_region_data_file(data_dir, PARTS_KIT_FILE_STEMS)
    if not path:
        return {}
    by_part = {}
    try:
        rows = _read_table(path)
    except Exception:
        return {}
    for row in rows:
        parent = str(
            _pick_fuzzy(row, PARTS_PARENT_KEYS + ["parent", "kit", "setsku"]) or ""
        ).strip()
        part = str(
            _pick_fuzzy(row, PARTS_COMPONENT_KEYS + ["part", "child", "sku", "partsku"]) or ""
        ).strip()
        if not parent or not part:
            continue
        qty = _to_float(_pick_fuzzy(row, PARTS_PER_SET_KEYS)) or 1.0
        if qty <= 0:
            qty = 1.0
        by_part[_norm_code(part)] = (parent, qty)
    return {"by_part": by_part, "path": str(path)}


def _apply_parts_kit_bom(detail, bom):
    if not detail or not bom:
        return detail
    by_part = bom.get("by_part") or {}
    if not by_part:
        return detail
    for line in detail:
        key = _norm_code(line.get("part") or "")
        hit = by_part.get(key)
        if hit:
            line["parent"] = hit[0]
            line["need_per_set"] = hit[1]
            line["parent_source"] = "bom"
    return detail


def _parts_identity_from_row(row, code):
    """借调成套按 PartName（Excel E 列）区分子件，不用 SKU 前缀合并不同成品。"""
    part_name = str(_pick_fuzzy(row, WIDE_PART_NAME_KEYS) or "").strip()
    comp_sku = str(_pick_fuzzy(row, PARTS_COMPONENT_KEYS) or "").strip()
    if part_name:
        return part_name
    if comp_sku:
        return comp_sku
    return str(code or "").strip()


def _reparent_parts_detail(detail):
    """仅对无明确母件的长表行，按 SKU 前缀 / ProductFamily 尝试归组（宽表母件 SKU 不改动）。"""
    if not detail:
        return detail

    def _locked(line):
        return line.get("parent_source") in ("wide", "explicit", "bom")

    base_parts = {}
    for line in detail:
        if _locked(line):
            continue
        base = _infer_kit_parent_sku(line.get("part") or line.get("parent") or "")
        base_parts.setdefault(base, set()).add(line["part"])
    for line in detail:
        if _locked(line):
            continue
        base = _infer_kit_parent_sku(line.get("part") or line.get("parent") or "")
        if len(base_parts.get(base, ())) >= 2:
            line["parent"] = base
            line["parent_source"] = "inferred"

    fam_parts = {}
    fam_label = {}
    for line in detail:
        if _locked(line):
            continue
        fam = str(line.get("family") or "").strip()
        if not fam:
            continue
        key = fam.lower()
        fam_parts.setdefault(key, set()).add(line["part"])
        fam_label[key] = fam
    for line in detail:
        if _locked(line):
            continue
        fam = str(line.get("family") or "").strip().lower()
        if not fam or len(fam_parts.get(fam, ())) < 2:
            continue
        parent = line.get("parent") or ""
        same_parent = {l["part"] for l in detail if l.get("parent") == parent}
        if len(same_parent) < 2:
            line["parent"] = f"FAM:{fam_label.get(fam, fam)}"
            line["parent_source"] = "inferred"
    return detail


def describe_parts_transfer_gap(detail):
    if not detail:
        return "未解析出任何 parts 行"
    hub = sum(1 for line in detail if line.get("bucket") in ("carbine", "walls", "chch"))
    other = len(detail) - hub
    empty_wh = sum(1 for line in detail if not str(line.get("warehouse") or "").strip())
    parts_per_parent = {}
    for line in detail:
        parts_per_parent.setdefault(line["parent"], set()).add(line["part"])
    multi = sum(1 for parts in parts_per_parent.values() if len(parts) >= 2)
    hub_parents = 0
    for parent, parts in parts_per_parent.items():
        if len(parts) < 2:
            continue
        lines = [line for line in detail if line.get("parent") == parent]
        if any(line.get("bucket") in ("carbine", "walls", "chch") for line in lines):
            hub_parents += 1
    msg = (
        f"解析 {len(detail)} 行 · 三仓 {hub} 行 / 其他仓 {other} 行 · "
        f"多配件母件 {multi} 组（三仓有库存 {hub_parents} 组）"
    )
    if empty_wh:
        msg += f" · 缺仓名列 {empty_wh} 行"
    if other:
        samples = Counter(
            str(line.get("warehouse") or "（空）").strip() or "（空）"
            for line in detail
            if line.get("bucket") == "other"
        ).most_common(4)
        if samples:
            msg += " · 未归入三仓示例：" + "；".join(f"{name}({cnt})" for name, cnt in samples)
    return msg


def _warehouse_stock_key(name):
    """与 stock 多仓列 / 南北岛同一套识别逻辑。"""
    key = _norm_col_key(name)
    if not key:
        return None
    if "carbine" in key or "carbin" in key or key.startswith("cbn"):
        return "carbine"
    if "walls" in key or key.startswith("wall"):
        return "walls"
    if (
        "geraldconnelly" in key
        or ("gerald" in key and "connelly" in key)
        or key.startswith("gc")
        or "chch" in key
        or "christchurch" in key
        or "connelly" in key
        or "southisland" in key
    ):
        return "geraldconnelly"
    return None


def _transfer_bucket_from_stock_key(stock_key):
    if stock_key == "geraldconnelly":
        return "chch"
    if stock_key in ("carbine", "walls"):
        return stock_key
    return None


def _warehouse_hints_from_stock_rows(stock_raw_rows):
    """从 stock.csv 表头学习仓名列名片段（如 CarbineSt → carbine）。"""
    hints = {"carbine": set(), "walls": set(), "chch": set()}
    if not stock_raw_rows:
        return {}
    for col in stock_raw_rows[0].keys():
        stock_key = _classify_warehouse_column(col)
        bucket = _transfer_bucket_from_stock_key(stock_key)
        if not bucket:
            continue
        label = str(col).strip().lower()
        if label:
            hints[bucket].add(label)
        nk = _norm_col_key(col)
        if nk:
            hints[bucket].add(nk)
    return {bucket: tuple(sorted(values)) for bucket, values in hints.items() if values}


def _load_warehouse_bucket_overrides(data_dir):
    """Data-NZ/warehouse_buckets.csv：warehouse_name,bucket（carbine/walls/chch）。"""
    if not data_dir:
        return {}
    path = _find_region_data_file(data_dir, WAREHOUSE_BUCKET_FILE_STEMS)
    if not path:
        return {}
    overrides = {}
    try:
        for row in _read_table(path):
            name = str(_pick_fuzzy(row, STORE_KEYS + ["warehousename", "name", "alias"]) or "").strip()
            bucket = str(_pick(row, ["bucket", "hub", "type"]) or _pick_fuzzy(row, ["bucket", "hub"]) or "").strip().lower()
            if not name or not bucket:
                continue
            if bucket in ("gc", "gerald", "geraldconnelly", "south"):
                bucket = "chch"
            if bucket not in ("carbine", "walls", "chch"):
                continue
            overrides[name.lower()] = bucket
    except Exception:
        return {}
    return overrides


def _warehouse_transfer_bucket(warehouse_name, hints=None, overrides=None):
    text = str(warehouse_name or "").strip()
    if not text:
        return "other"
    low = text.lower()
    if overrides and low in overrides:
        return overrides[low]
    stock_key = _warehouse_stock_key(text)
    bucket = _transfer_bucket_from_stock_key(stock_key)
    if bucket:
        return bucket
    for bucket, tokens in TRANSFER_WAREHOUSE_BUCKETS:
        if any(token in low for token in tokens):
            return bucket
    if hints:
        for bucket, fragments in hints.items():
            for frag in fragments:
                frag = str(frag or "").lower()
                if frag and (frag in low or _norm_col_key(frag) in _norm_col_key(text)):
                    return bucket
    return "other"


def _apply_warehouse_buckets(detail, hints=None, overrides=None):
    for line in detail or []:
        line["bucket"] = _warehouse_transfer_bucket(
            line.get("warehouse"), hints=hints, overrides=overrides,
        )
    return detail


def _classify_parts_hub_column(col_name):
    """宽表列名：Carbin 库存 / Walls 库存 / CHCH 库存 → 借调三仓。"""
    key = _norm_col_key(col_name)
    if not key:
        return None
    if "carbin" in key or "carbine" in key or key.startswith("cbn"):
        return "carbine"
    if "walls" in key or key.startswith("wall"):
        return "walls"
    if "chch" in key or key == "gc" or key.startswith("gc") or "gerald" in key:
        return "chch"
    return None


def _hub_qty_columns_from_row(sample_row):
    cols = []
    for key in sample_row.keys():
        bucket = _classify_parts_hub_column(key)
        if bucket:
            cols.append((key, bucket))
    return cols


def _parse_parts_wide_hub_rows(rows):
    """
    Excel 宽表：每行一个子件，Carbin/Walls/CHCH 库存分列（如「Carbin 库存」）。
    """
    if not rows:
        return []
    hub_cols = _hub_qty_columns_from_row(rows[0])
    if len(hub_cols) < 2:
        return []
    detail = []
    for row in rows:
        parent = str(
            _pick_fuzzy(row, PARTS_PARENT_KEYS) or _pick(row, CODE_KEYS) or ""
        ).strip()
        if not parent:
            parent = str(_mining_code_from_row(row) or "").strip()
        if not parent:
            continue
        part = _parts_identity_from_row(row, parent)
        if not part:
            part = "（未标注部件）"
        name = str(
            _pick(row, NAME_KEYS) or _pick_fuzzy(row, NAME_KEYS + ["productna", "productname"]) or ""
        ).strip()
        need = _to_float(_pick_fuzzy(row, PARTS_PER_SET_KEYS)) or 1.0
        if need <= 0:
            need = 1.0
        for col_name, bucket in hub_cols:
            qty = _to_float(row.get(col_name))
            if qty is None or qty <= 0:
                continue
            detail.append({
                "parent": parent,
                "part": part,
                "name": name,
                "family": str(_pick(row, FAMILY_KEYS) or "").strip(),
                "warehouse": str(col_name).strip(),
                "bucket": bucket,
                "qty": float(qty),
                "need_per_set": need,
                "parent_source": "wide",
            })
    return detail


def _parse_parts_detail_rows(rows):
    """解析 parts.csv：支持宽表三仓列，或长表 WarehouseName + Sku。"""
    wide = _parse_parts_wide_hub_rows(rows)
    if wide:
        return wide
    detail = []
    for row in rows:
        code = str(_mining_code_from_row(row) or "").strip()
        if not code:
            continue
        parent = str(_pick_fuzzy(row, PARTS_PARENT_KEYS) or "").strip()
        part = _parts_identity_from_row(row, code)
        parent_source = "explicit" if parent else "inferred"
        if not parent:
            parent = _infer_kit_parent_sku(code)
        warehouse = str(
            _pick(row, STORE_KEYS + ["warehousename"]) or _pick_fuzzy(row, STORE_KEYS + ["warehousename"]) or ""
        ).strip()
        qty = _mining_qty_from_row(row, PARTS_QTY_KEYS)
        need = _to_float(_pick_fuzzy(row, PARTS_PER_SET_KEYS)) or 1.0
        if need <= 0:
            need = 1.0
        detail.append({
            "parent": parent,
            "part": part,
            "name": str(_pick(row, NAME_KEYS) or _pick_fuzzy(row, NAME_KEYS) or "").strip(),
            "family": str(_pick(row, FAMILY_KEYS) or "").strip(),
            "warehouse": warehouse,
            "bucket": _warehouse_transfer_bucket(warehouse),
            "qty": qty,
            "need_per_set": need,
            "parent_source": parent_source,
        })
    return detail


def _load_parts_inventory_wide(rows):
    """宽表 parts：按母件 SKU 汇总三仓数量（供库存清单）。"""
    detail = _parse_parts_wide_hub_rows(rows)
    if not detail:
        return None
    by_code = {}
    for line in detail:
        parent = line["parent"]
        norm = _norm_code(parent)
        bucket = by_code.setdefault(norm, {
            "code": parent,
            "name": line.get("name") or "",
            "family": line.get("family") or "",
            "total_qty": 0.0,
            "warehouses": [],
        })
        bucket["total_qty"] += line["qty"]
        bucket["warehouses"].append({
            "warehouse": line.get("warehouse") or line.get("bucket"),
            "qty": line["qty"],
        })
    return by_code


def _pooled_complete_sets(kit, parts, buckets):
    if not parts:
        return 0
    vals = []
    for part_id, need in parts.items():
        if need <= 0:
            continue
        total = sum(kit["inv"].get((part_id, b), 0.0) for b in buckets)
        vals.append(total / need)
    if not vals:
        return 0
    return int(min(vals))


def _north_assembly_hub(sets_by):
    """北岛借调优先在 Carbine / Walls 间调配，缺件侧为组装仓。"""
    sc = int(sets_by.get("carbine") or 0)
    sw = int(sets_by.get("walls") or 0)
    return "walls" if sw > sc else "carbine"


def _transfer_flows_between_hubs(kit, parts, target_sets, hub, other):
    """为在北岛凑 target_sets 套，从 other 仓调部件到 hub 仓（仅 Carbine↔Walls）。"""
    flows = []
    if target_sets <= 0:
        return flows
    for part_id, need in sorted(parts.items()):
        if need <= 0:
            continue
        req = need * target_sets
        q_hub = kit["inv"].get((part_id, hub), 0.0)
        q_other = kit["inv"].get((part_id, other), 0.0)
        if q_hub + q_other < req:
            continue
        move = req - q_hub
        if move >= 1:
            flows.append((other, hub, part_id, int(move)))
    return flows


def _transfer_flows_from_chch(kit, parts, extra_sets, hub):
    """北岛仍凑不齐时，从 CHCH 调南岛件到北岛组装仓。"""
    flows = []
    if extra_sets <= 0:
        return flows
    for part_id, need in sorted(parts.items()):
        if need <= 0:
            continue
        req = need * extra_sets
        qh = kit["inv"].get((part_id, "chch"), 0.0)
        qty = int(min(qh, req))
        if qty > 0:
            flows.append(("chch", hub, part_id, qty))
    return flows


def _format_transfer_flows(flows, max_lines=5):
    if not flows:
        return ""
    labels = {"carbine": "Carbine", "walls": "Walls", "chch": "CHCH"}
    chunks = []
    for src, dst, part_id, qty in flows[:max_lines]:
        chunks.append(f"{labels.get(src, src)}→{labels.get(dst, dst)} {part_id}×{qty}")
    text = "; ".join(chunks)
    if len(flows) > max_lines:
        text += f" 等{len(flows)}项"
    return text


def analyze_parts_transfer(rows, parts_kit_bom=None, warehouse_hints=None, warehouse_overrides=None):
    """
    跨 Carbine / Walls / CHCH 借调拼凑：比较各仓独立成套数 vs 三仓合并后最多成套数。
    可传入 parts.csv 原始行，或 bundle 内已解析的 parts_detail_rows。
    """
    if rows and isinstance(rows[0], dict) and rows[0].get("bucket") is not None and rows[0].get("part"):
        detail = list(rows)
    else:
        detail = _parse_parts_detail_rows(rows)
    if not detail:
        return []
    detail = _apply_parts_kit_bom(detail, parts_kit_bom or {})
    detail = _reparent_parts_detail(detail)
    detail = _apply_warehouse_buckets(detail, warehouse_hints, warehouse_overrides)
    parts_per_parent = {}
    for line in detail:
        parts_per_parent.setdefault(line["parent"], set()).add(line["part"])
    kits = {}
    for line in detail:
        parent = line["parent"]
        kit = kits.setdefault(parent, {
            "parent": parent,
            "name": line.get("name") or "",
            "family": line.get("family") or "",
            "parts": {},
            "inv": {},
        })
        if not kit["name"] and line.get("name"):
            kit["name"] = line["name"]
        part_id = line["part"]
        kit["parts"][part_id] = max(kit["parts"].get(part_id, 0), line["need_per_set"])
        key = (part_id, line["bucket"])
        kit["inv"][key] = kit["inv"].get(key, 0.0) + line["qty"]

    hub_buckets = ("carbine", "walls", "chch")
    results = []
    for parent, kit in kits.items():
        if len(parts_per_parent.get(parent, ())) < 2:
            continue
        parts = kit["parts"]
        sets_by = {}
        for bucket in hub_buckets:
            sets_by[bucket] = min(
                kit["inv"].get((part_id, bucket), 0.0) / need
                for part_id, need in parts.items()
            )
            sets_by[bucket] = int(sets_by[bucket])
        current_total = sum(sets_by[bucket] for bucket in hub_buckets)
        north_buckets = ("carbine", "walls")
        after_north = _pooled_complete_sets(kit, parts, north_buckets)
        after_transfer = _pooled_complete_sets(kit, parts, hub_buckets)
        gain = after_transfer - current_total
        gain_north = after_north - current_total
        gain_south = after_transfer - after_north
        hub = _north_assembly_hub(sets_by)
        other = "walls" if hub == "carbine" else "carbine"
        if gain_north > 0 or gain_south > 0:
            north_txt = _format_transfer_flows(
                _transfer_flows_between_hubs(kit, parts, after_north, hub, other),
            )
            south_txt = _format_transfer_flows(
                _transfer_flows_from_chch(kit, parts, gain_south, hub),
            ) if gain_south > 0 else ""
            if north_txt and south_txt:
                transfer_plan = f"①北岛 {north_txt}；②南岛补位 {south_txt}"
            elif north_txt:
                transfer_plan = f"北岛 {north_txt}"
            elif south_txt:
                transfer_plan = f"南岛→北岛 {south_txt}"
            else:
                transfer_plan = ""
        else:
            transfer_plan = ""
        if current_total <= 0 and after_transfer <= 0:
            continue
        dist_parts = []
        for part_id in sorted(parts):
            chunks = []
            for bucket in hub_buckets:
                q = int(kit["inv"].get((part_id, bucket), 0))
                if q:
                    chunks.append(f"{bucket}:{q}")
            if chunks:
                dist_parts.append(f"{part_id} " + "+".join(chunks))
        display_parent = parent[4:] if str(parent).startswith("FAM:") else parent
        display_name = kit.get("name") or display_parent
        results.append({
            "parent": display_parent,
            "name": display_name,
            "part_count": len(parts),
            "sets_carbine": sets_by["carbine"],
            "sets_walls": sets_by["walls"],
            "sets_chch": sets_by["chch"],
            "sets_current_total": current_total,
            "sets_after_north": after_north,
            "sets_after_transfer": after_transfer,
            "transfer_gain": gain,
            "transfer_gain_north": gain_north,
            "transfer_gain_south": gain_south,
            "transfer_plan": transfer_plan or "-",
            "parts_distribution": "; ".join(dist_parts[:6]),
        })
    results.sort(key=lambda r: (-r["transfer_gain"], -r["sets_after_transfer"], r["parent"]))
    return results


def list_mining_inventory(bundle, kind="all"):
    """返回 On Hold / 配件挖掘列表（用于独立标签页表格）。"""
    rows = []
    if kind in ("all", "on_hold"):
        for item in (bundle.get("on_hold_by_code") or {}).values():
            rows.append({
                "kind": "On Hold",
                "code": item["code"],
                "name": item.get("name") or "",
                "family": item.get("family") or "",
                "qty": item.get("total_qty") or 0,
                "detail": "、".join(sorted(item.get("statuses") or [])) or "-",
                "warehouses": item.get("warehouses") or [],
                "hold_days": item.get("max_hold_days"),
                "hold_since": _format_hold_since(item.get("earliest_hold_at")),
            })
    if kind in ("all", "parts"):
        for item in (bundle.get("parts_by_code") or {}).values():
            rows.append({
                "kind": "配件",
                "code": item["code"],
                "name": item.get("name") or "",
                "family": item.get("family") or "",
                "qty": item.get("total_qty") or 0,
                "detail": "-",
                "warehouses": item.get("warehouses") or [],
            })
    rows.sort(key=lambda r: (-float(r.get("qty") or 0), r.get("kind", ""), r.get("code", "")))
    return rows


def _load_display(rows):
    """返回 (by_store, display_details)。

    by_store: {store_name: set(codes)}
    display_details: {store_name: {norm_code: {code, family, name}}}
    """
    by_store = {}
    display_details = {}
    for row in rows:
        code = _pick(row, CODE_KEYS)
        if not code:
            continue
        store = _pick(row, STORE_KEYS)
        store = str(store).strip() if store else "（未标注店面）"
        norm = _norm_code(code)
        by_store.setdefault(store, set()).add(norm)
        display_details.setdefault(store, {})[norm] = {
            "code": str(code).strip(),
            "family": str(_pick(row, FAMILY_KEYS) or "").strip(),
            "name": str(_pick(row, NAME_KEYS) or "").strip(),
        }
    return by_store, display_details


def _load_exemption_config():
    global _EXEMPTION_CONFIG_CACHE
    if _EXEMPTION_CONFIG_CACHE is not None:
        return _EXEMPTION_CONFIG_CACHE
    if not EXEMPTION_CONFIG_FILE.is_file():
        _EXEMPTION_CONFIG_CACHE = {"auto_by_family": True, "groups": []}
        return _EXEMPTION_CONFIG_CACHE
    try:
        with EXEMPTION_CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        _EXEMPTION_CONFIG_CACHE = data if isinstance(data, dict) else {"auto_by_family": True, "groups": []}
    except Exception:
        _EXEMPTION_CONFIG_CACHE = {"auto_by_family": True, "groups": []}
    return _EXEMPTION_CONFIG_CACHE


def _norm_group_token(text):
    return re.sub(r"\s+", " ", str(text).strip().upper())


def _product_matches_group(product, group):
    fam = _norm_group_token(product.get("family") or "")
    name = str(product.get("name") or "").lower()
    for f in group.get("families") or []:
        if fam == _norm_group_token(f):
            return True
    for pat in group.get("name_patterns") or []:
        if str(pat).lower() in name:
            return True
    return False


def _is_uncategorized_family(family):
    text = str(family or "").strip()
    if not text:
        return True
    upper = text.upper()
    return text in UNCATEGORIZED_FAMILIES or upper in UNCATEGORIZED_FAMILIES


def _resolve_exemption_group(product, config):
    for group in config.get("groups") or []:
        if _product_matches_group(product, group):
            label = (group.get("label") or group.get("key") or "").strip()
            key = (group.get("key") or label or "").strip()
            if _is_uncategorized_family(label) or _is_uncategorized_family(key):
                return None, label or "未分类"
            return _norm_group_token(key), label or key
    if config.get("auto_by_family", True):
        fam = str(product.get("family") or "未分类").strip()
        if _is_uncategorized_family(fam):
            return None, fam or "未分类"
        return _norm_group_token(fam), fam
    return None, None


def _anchors_by_group_from_display(displayed_details, config):
    """按 exemption 组索引店面 display 表里的已展示 SKU（每店面只算一次）。"""
    by_group = {}
    for norm, meta in (displayed_details or {}).items():
        pseudo = {
            "code": meta.get("code") or norm,
            "family": meta.get("family") or "",
            "name": meta.get("name") or "",
        }
        gkey, _ = _resolve_exemption_group(pseudo, config)
        if gkey:
            by_group.setdefault(gkey, []).append(pseudo["code"])
    return by_group


def _display_anchor_codes(members, display_anchors_by_group=None, displayed_details=None, config=None):
    """同组内在该店面已展示的 SKU（含 display 表有、stock 表无的款）。"""
    group_key = next((m.get("exemption_group") for m in members if m.get("exemption_group")), None)
    if not group_key:
        return []

    codes = []
    seen = set()
    for m in members:
        if not m.get("displayed"):
            continue
        code = m.get("code")
        norm = _norm_code(code)
        if norm and norm not in seen:
            seen.add(norm)
            codes.append(code)

    if display_anchors_by_group is not None:
        for code in display_anchors_by_group.get(group_key, []):
            norm = _norm_code(code)
            if norm and norm not in seen:
                seen.add(norm)
                codes.append(code)
        return codes

    for norm, meta in (displayed_details or {}).items():
        if norm in seen:
            continue
        pseudo = {
            "code": meta.get("code") or norm,
            "family": meta.get("family") or "",
            "name": meta.get("name") or "",
        }
        gkey, _ = _resolve_exemption_group(pseudo, config)
        if gkey == group_key:
            seen.add(norm)
            codes.append(pseudo["code"])
    return codes


def _eligible_for_exemption(product):
    if product.get("displayed") or not product.get("in_stock"):
        return False
    if product.get("gap"):
        return True
    return bool(product.get("discontinued"))


def _enrich_catalog_metadata(rows):
    """产品系列/豁免组与店面无关，在载入 stock 时就算好，切换店面时复用。"""
    config = _load_exemption_config()
    for p in rows:
        if p.get("_catalog_enriched"):
            continue
        gkey, glabel = _resolve_exemption_group(p, config)
        p["exemption_group"] = gkey
        p["exemption_group_label"] = glabel
        p["_catalog_enriched"] = True


def _apply_family_exemptions(products, displayed_details=None):
    """同 exemption 组内已有展示 SKU 时，豁免组内其他有货未展示 SKU。

    锚点条件：在所选店面「已展示」即可（含 display 表有展示、stock 表无库存的款），
    不要求锚点有货。停产但有货未展示的 SKU 也可被同组已展示款豁免。
    """
    config = _load_exemption_config()
    display_anchors = _anchors_by_group_from_display(displayed_details, config)
    for p in products:
        p["exempted"] = False
        p["exemption_reason"] = ""
        if not p.get("_catalog_enriched"):
            gkey, glabel = _resolve_exemption_group(p, config)
            p["exemption_group"] = gkey
            p["exemption_group_label"] = glabel

    by_group = {}
    for p in products:
        gk = p.get("exemption_group")
        if gk:
            by_group.setdefault(gk, []).append(p)

    exempted_n = 0
    for members in by_group.values():
        anchor_codes = _display_anchor_codes(members, display_anchors_by_group=display_anchors)
        if not anchor_codes:
            continue
        reason = f"同组已展示：{anchor_codes[0]}"
        if len(anchor_codes) > 1:
            extra = ", ".join(anchor_codes[1:3])
            suffix = "…" if len(anchor_codes) > 3 else ""
            reason = f"同组已展示：{anchor_codes[0]}, {extra}{suffix}"
        for m in members:
            if not _eligible_for_exemption(m):
                continue
            m["exempted"] = True
            if m.get("gap"):
                m["gap"] = False
            m["exemption_reason"] = reason
            exempted_n += 1
    return exempted_n


def list_stores(region=None):
    bundle = _load_region_bundle(region or default_region())
    by_store = bundle["by_store"]
    return [ALL_STORES] + sorted(by_store.keys())


SKU_PREFIX_LEN = 3


def sku_prefix(code, length=SKU_PREFIX_LEN):
    """取 SKU/编码前 N 位作为分类前缀（如 107-381 → 107）。"""
    text = str(code or "").strip().upper()
    return text[:length] if text else "???"


def aggregate_by_sku_prefix(products, prefix_len=SKU_PREFIX_LEN, store_specific=True,
                              owner_config=None):
    """按 SKU 前三位汇总；产品数仅计在产（non-discontinue）SKU。"""
    buckets = {}
    for p in products:
        if p.get("discontinued"):
            continue
        key = sku_prefix(p.get("code"), prefix_len)
        buckets.setdefault(key, []).append(p)

    rows = []
    for prefix, items in buckets.items():
        total = len(items)
        in_stock = [i for i in items if i.get("in_stock")]
        in_stock_n = len(in_stock)
        displayed_is = [i for i in in_stock if i.get("displayed")]
        if store_specific:
            gap_items = [i for i in items if i.get("gap")]
            exempted = [i for i in items if i.get("exempted")]
        else:
            gap_items = []
            exempted = []
        rows.append({
            "prefix": prefix,
            "owner": _owner_for_prefix(prefix, owner_config) if owner_config else "",
            "total": total,
            "in_stock_count": in_stock_n,
            "in_stock_rate": round(in_stock_n / total * 100, 1) if total else None,
            "displayed_in_stock": len(displayed_is),
            "display_coverage_rate": (
                round(len(displayed_is) / in_stock_n * 100, 1) if in_stock_n else None
            ),
            "gap_count": len(gap_items),
            "exempted_count": len(exempted),
        })
    rows.sort(key=lambda r: (-(r["in_stock_rate"] or 0), -r["gap_count"], r["prefix"]))
    return rows


def build_products(store=None, only_gap=False, include_discontinued=False, region=None,
                   force_refresh=False):
    """核心：按店面逐个产品计算 有货/展示 状态与汇总指标。"""
    region_key = region or default_region()
    if not region_key:
        raise ValueError("region_runner_config.json 中未配置任何地区。")

    bundle = _load_region_bundle(region_key, force=force_refresh)
    full_stock = bundle.get("discontinued_loaded") or include_discontinued
    if full_stock and not bundle.get("discontinued_loaded"):
        _ensure_discontinued_rows(bundle)
    view_key = (
        region_key,
        store or ALL_STORES,
        bool(full_stock),
        bundle.get("stock_mtime"),
        bundle.get("display_mtime"),
        bundle.get("blacklist_mtime"),
        bundle.get("stock_discontinued_mtime"),
        bundle.get("storage_mtime"),
        bundle.get("on_hold_mtime"),
        bundle.get("parts_mtime"),
    )
    if not force_refresh and view_key in _STORE_VIEW_CACHE:
        cached = _STORE_VIEW_CACHE[view_key]
        if only_gap:
            out = dict(cached)
            out["products"] = [p for p in cached["products"] if p["gap"]]
            return out
        return cached

    stock_rows = bundle["stock_rows"]
    if full_stock:
        iter_rows = stock_rows
    else:
        iter_rows = bundle.get("stock_rows_active") or stock_rows
    by_store = bundle["by_store"]
    stock_path = bundle["stock_path"]
    display_path = bundle["display_path"]
    source = bundle["source"]
    data_dir = bundle.get("data_dir")
    blacklist = bundle.get("blacklist") or set()
    blacklist_path = bundle.get("blacklist_path")

    stores = [ALL_STORES] + sorted(by_store.keys())
    if store is None:
        store = ALL_STORES

    if store == ALL_STORES:
        displayed_codes = set().union(*by_store.values()) if by_store else set()
    else:
        displayed_codes = by_store.get(store, set())

    store_specific = store != ALL_STORES
    catalog_wh_keys = _collect_catalog_warehouse_keys(iter_rows)
    warehouse_keys = _warehouses_for_store(store, region_key, catalog_wh_keys)
    by_storage = bundle.get("by_storage") or {}
    storage_details = bundle.get("storage_details") or {}
    storage_map = bundle.get("storage_map") or {}
    storage_unmapped = bundle.get("storage_unmapped") or []
    has_storage_data = bool(bundle.get("storage_path")) and Path(bundle["storage_path"]).is_file()
    all_storage_skus = len(set().union(*by_storage.values())) if by_storage else 0
    storage_codes = by_storage.get(store, set()) if store_specific else set()
    store_storage_details = storage_details.get(store, {}) if store_specific else {}
    on_hold_by_code = bundle.get("on_hold_by_code") or {}
    parts_by_code = bundle.get("parts_by_code") or {}
    has_on_hold_data = bool(bundle.get("on_hold_path")) and Path(bundle["on_hold_path"]).is_file()
    has_parts_data = bool(bundle.get("parts_path")) and Path(bundle["parts_path"]).is_file()

    products = []
    for p in iter_rows:
        if p.get("norm_code") in blacklist:
            continue
        if not full_stock and p.get("discontinued"):
            continue
        item = _apply_store_stock(p, store, region_key, warehouse_keys)
        displayed = item["norm_code"] in displayed_codes
        in_storage = item["norm_code"] in storage_codes if has_storage_data else None
        storage_qty = store_storage_details.get(item["norm_code"], {}).get("qty", 0)
        if store_specific:
            gap = item["in_stock"] and (not item["discontinued"]) and (not displayed)
            warehouse_only = bool(
                has_storage_data and item["in_stock"] and not in_storage and not displayed
            )
            ready_not_displayed = bool(
                has_storage_data
                and item["in_stock"]
                and in_storage
                and not displayed
                and not item["discontinued"]
            )
        else:
            gap = False
            warehouse_only = False
            ready_not_displayed = False
        item["displayed"] = displayed
        item["gap"] = gap
        item["in_storage"] = in_storage
        item["storage_qty"] = storage_qty if in_storage else 0
        item["warehouse_only"] = warehouse_only
        item["ready_not_displayed"] = ready_not_displayed
        if island_stock_supported(region_key):
            _enrich_island_stock(item, region_key)
        norm = item.get("norm_code")
        oh = on_hold_by_code.get(norm) if norm else None
        item["on_hold"] = bool(oh)
        item["on_hold_qty"] = float(oh.get("total_qty") or 0) if oh else 0.0
        item["on_hold_status"] = (
            "、".join(sorted(oh.get("statuses") or [])) if oh else ""
        )
        pt = parts_by_code.get(norm) if norm else None
        item["is_part"] = bool(pt)
        item["parts_qty"] = float(pt.get("total_qty") or 0) if pt else 0.0
        products.append(item)

    if store_specific:
        store_display_details = bundle.get("display_details", {}).get(store, {})
        exempted_count = _apply_family_exemptions(products, store_display_details)
    else:
        config = _load_exemption_config()
        for p in products:
            p["exempted"] = False
            p["exemption_reason"] = ""
            if not p.get("_catalog_enriched"):
                gkey, glabel = _resolve_exemption_group(p, config)
                p["exemption_group"] = gkey
                p["exemption_group_label"] = glabel
        exempted_count = 0

    total_nd = 0
    in_stock_n = 0
    displayed_in_stock_n = 0
    not_displayed_n = 0
    raw_gap_active_n = 0
    in_stock_not_displayed_all_n = 0
    in_stock_not_displayed_discontinued_n = 0
    warehouse_only_n = 0
    ready_not_displayed_n = 0
    in_storage_n = 0
    island_quadrant_counts = {key: 0 for key in ISLAND_STOCK_CLASSES}
    for p in products:
        disc = bool(p.get("discontinued"))
        in_stock = bool(p.get("in_stock"))
        displayed = bool(p.get("displayed"))
        exempted = bool(p.get("exempted"))
        gap = bool(p.get("gap"))
        if not disc:
            total_nd += 1
            if in_stock:
                in_stock_n += 1
                if displayed:
                    displayed_in_stock_n += 1
                if gap:
                    not_displayed_n += 1
        if store_specific:
            if in_stock and not disc and not displayed:
                raw_gap_active_n += 1
            if in_stock and not displayed and not exempted:
                in_stock_not_displayed_all_n += 1
                if disc:
                    in_stock_not_displayed_discontinued_n += 1
            if p.get("in_storage"):
                in_storage_n += 1
            if p.get("warehouse_only") and not exempted:
                warehouse_only_n += 1
            if p.get("ready_not_displayed") and not exempted:
                ready_not_displayed_n += 1
        if not disc and p.get("island_stock_class"):
            island_quadrant_counts[p["island_stock_class"]] += 1

    summary = {
        "store": store,
        "region": region_key,
        "store_specific": store_specific,
        "total_non_discontinue": total_nd,
        "in_stock_count": in_stock_n,
        "in_stock_rate": round(in_stock_n / total_nd * 100, 2) if total_nd else None,
        "displayed_in_stock_count": displayed_in_stock_n,
        "display_coverage_rate": (
            round(displayed_in_stock_n / in_stock_n * 100, 2) if in_stock_n else None
        ),
        "raw_gap_active_count": raw_gap_active_n if store_specific else None,
        "not_displayed_count": not_displayed_n if store_specific else None,
        "exempted_count": exempted_count if store_specific else None,
        "in_stock_not_displayed_all": in_stock_not_displayed_all_n if store_specific else None,
        "in_stock_not_displayed_discontinued": (
            in_stock_not_displayed_discontinued_n if store_specific else None
        ),
        "stock_sources": " + ".join(_warehouse_label(k) for k in warehouse_keys),
        "stock_files": _stock_files_label(bundle),
        "data_files": _panel_data_files_label(bundle),
        "image_url_count": _count_image_urls(iter_rows),
        "blacklist_count": len(blacklist),
        "blacklist_path": blacklist_path,
        "blacklist_file_found": bool(blacklist_path),
        "blacklist_expected_path": str(expected_blacklist_path(region_key)),
        "data_format": Path(stock_path).suffix.lower(),
        "uses_split_stock": bool(bundle.get("stock_discontinued_path")),
        "has_storage_data": has_storage_data,
        "storage_row_count": bundle.get("storage_row_count", 0),
        "storage_map": storage_map,
        "storage_unmapped": storage_unmapped,
        "storage_warehouse": storage_map.get(store) if store_specific else None,
        "all_storage_sku_count": all_storage_skus if has_storage_data else 0,
        "in_storage_count": in_storage_n if store_specific else None,
        "warehouse_only_count": warehouse_only_n if store_specific else None,
        "ready_not_displayed_count": ready_not_displayed_n if store_specific else None,
        "includes_discontinued": bool(full_stock),
        "island_stock_supported": island_stock_supported(region_key),
        "island_quadrant_counts": island_quadrant_counts if island_stock_supported(region_key) else None,
        "has_on_hold_data": has_on_hold_data,
        "has_parts_data": has_parts_data,
        "on_hold_sku_count": len(on_hold_by_code),
        "parts_sku_count": len(parts_by_code),
        "on_hold_path": bundle.get("on_hold_path"),
        "parts_path": bundle.get("parts_path"),
    }

    products.sort(key=lambda p: (
        p.get("exemption_group_label") or p.get("family") or "未分类",
        not p["gap"],
        not p.get("exempted"),
        not p["in_stock"],
        -p["stock_qty"],
        p["code"],
    ))

    diagnostics = []
    display_row_count = bundle["display_row_count"]
    if display_row_count < 5:
        diagnostics.append({
            "level": "warning",
            "message": (
                f"展示数据几乎为空（display 仅 {display_row_count} 行）："
                f"{display_path}。请检查 Data-NZ/display_with_families.sql 并重新执行导出。"
            ),
        })
    elif len(by_store) == 0:
        diagnostics.append({
            "level": "warning",
            "message": (
                "展示数据里没有识别到店面列（需要 Store / DisplayWarehouse / Warehouse 等列名）。"
                f"当前文件：{display_path}"
            ),
        })
    elif len(stores) <= 1:
        diagnostics.append({
            "level": "warning",
            "message": "展示数据未包含有效店面，店面下拉只会显示「全部店面」。",
        })
    if has_storage_data and storage_unmapped:
        diagnostics.append({
            "level": "warning",
            "message": (
                "部分店后仓未能对齐到陈列店面："
                + "、".join(storage_unmapped[:5])
                + ("…" if len(storage_unmapped) > 5 else "")
                + "。请检查 storage.csv 的 WarehouseName 与 display.csv 是否一致。"
            ),
        })
    elif has_storage_data and storage_map:
        mapped = "；".join(f"{wh}→{store}" for store, wh in sorted(storage_map.items()))
        diagnostics.append({
            "level": "info",
            "message": f"店后仓已映射：{mapped}",
        })
    if not has_on_hold_data:
        diagnostics.append({
            "level": "info",
            "message": (
                "未找到 on_hold.csv：可在 Data-NZ 放置 on_hold.txt 并执行 SQL 导出，"
                "用于 On Hold 挖掘。"
            ),
        })
    if not has_parts_data:
        diagnostics.append({
            "level": "info",
            "message": (
                "未找到 parts.csv：可在 Data-NZ 放置 parts.txt 并执行 SQL 导出，"
                "用于配件挖掘。"
            ),
        })

    result = {
        "source": source,
        "region": region_key,
        "stock_path": str(stock_path),
        "display_path": str(display_path),
        "blacklist_path": blacklist_path,
        "blacklist_count": len(blacklist),
        "blacklist_file_found": bool(blacklist_path),
        "blacklist_expected_path": str(expected_blacklist_path(region_key)),
        "data_dir": str(bundle.get("data_dir") or ""),
        "data_format": Path(stock_path).suffix.lower(),
        "display_row_count": display_row_count,
        "stores": stores,
        "selected_store": store,
        "summary": summary,
        "products": products,
        "regions": list_regions(),
        "diagnostics": diagnostics,
    }
    _STORE_VIEW_CACHE[view_key] = result
    if only_gap:
        out = dict(result)
        out["products"] = [p for p in products if p["gap"]]
        return out
    return result


def prewarm_store_views(region, stores=None, include_discontinued=True):
    """后台预热多个店面视图，切换店面时直接走缓存。"""
    region_key = str(region).strip().upper()
    bundle = _load_region_bundle(region_key)
    if include_discontinued and not bundle.get("discontinued_loaded"):
        _ensure_discontinued_rows(bundle)
    if stores is None:
        stores = sorted(bundle.get("by_store", {}).keys())
    warmed = []
    for store in stores:
        if store == ALL_STORES:
            continue
        view_key = (
            region_key,
            store,
            bool(include_discontinued or bundle.get("discontinued_loaded")),
            bundle.get("stock_mtime"),
            bundle.get("display_mtime"),
            bundle.get("blacklist_mtime"),
            bundle.get("stock_discontinued_mtime"),
            bundle.get("storage_mtime"),
        )
        if view_key in _STORE_VIEW_CACHE:
            continue
        build_products(
            store=store,
            include_discontinued=include_discontinued,
            region=region_key,
        )
        warmed.append(store)
    return warmed


if __name__ == "__main__":
    import json as _json
    data = build_products(store=ALL_STORES, region=default_region())
    print("stores:", data["stores"][:5], "...")
    print("summary:", _json.dumps(data["summary"], ensure_ascii=False))
