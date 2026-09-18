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

# 启动时预加载停产 SKU（停产为常态时避免每次切筛选都重算）
EAGER_DISCONTINUED_STOCK = os.getenv("PANEL_EAGER_DISCONTINUED", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

CODE_KEYS = ["productcode", "product_code", "sku", "itemcode", "item_code",
             "code", "productid", "product_id", "product", "item"]
BLACKLIST_STEMS = ["blacklist", "sku_blacklist", "black_list", "product_blacklist"]
BLACKLIST_KEYS = ["sku", "blacklist", "blacklistsku"] + CODE_KEYS
CHANNEL_OWNER_STEMS = ["channel_owners", "sku_owners", "owner_channels", "channel_owner"]
OWNER_KEYS = ["owner", "负责人", "person", "personincharge"]
CHANNEL_KEYS = ["channel", "渠道", "sku_prefix", "prefix", "channelcode"]
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


def _pick(row, keys):
    lower = {str(k).strip().lower(): v for k, v in row.items()}
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
    return {
        **product,
        "stock_qty": qty,
        "in_stock": qty > 0,
        "stock_warehouses": warehouse_keys,
        "stock_breakdown": breakdown,
    }


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


STORAGE_QTY_KEYS = ["storageqty", "storage_qty", "qty", "quantity", "displayqty"]


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
    return _find_region_data_file(data_dir, CHANNEL_OWNER_STEMS)


def _load_channel_owner_config(path):
    rows = []
    if not path or not Path(path).is_file():
        return rows
    for row in _read_table(path):
        owner = str(_pick(row, OWNER_KEYS) or "").strip()
        channel = str(_pick(row, CHANNEL_KEYS) or "").strip()
        if not owner or not channel:
            continue
        lead_raw = _pick(row, LEAD_TIME_KEYS)
        lead_time = None
        if lead_raw is not None and str(lead_raw).strip() != "":
            try:
                lead_time = int(float(str(lead_raw).strip()))
            except ValueError:
                lead_time = str(lead_raw).strip()
        rows.append({
            "owner": owner,
            "channel": channel,
            "lead_time": lead_time,
            "merge_products": str(_pick(row, MERGE_PRODUCT_KEYS) or "所有").strip() or "所有",
            "merge_regions": str(_pick(row, MERGE_REGION_KEYS) or "").strip(),
            "note": str(_pick(row, OWNER_NOTE_KEYS) or "").strip(),
        })
    return rows


def load_channel_owner_config(region=None, data_dir=None):
    region_key = str(region or default_region() or "NZ").strip().upper()
    base = Path(data_dir) if data_dir else _region_output_dir(region_key)
    path = _find_channel_owner_file(base)
    rows = _load_channel_owner_config(path)
    return rows, str(path) if path else None


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

    cached = _REGION_CACHE.get(region_key)
    if (
        not force
        and cached
        and cached["stock_mtime"] == stock_mtime
        and cached["display_mtime"] == display_mtime
        and cached.get("blacklist_mtime") == blacklist_mtime
        and cached.get("stock_discontinued_mtime") == disc_mtime
        and cached.get("storage_mtime") == storage_mtime
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
    blacklist = _load_blacklist(blacklist_path)
    stock_raw_rows = _read_table(stock_path)
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
    return " + ".join(parts)


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
