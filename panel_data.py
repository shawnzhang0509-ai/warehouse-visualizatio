"""有货未展示看板 —— 纯本地数据层（不依赖任何网页/Flask）。

核心：用店面展示数据 (display) 比对 non-discontinue 且有货的产品 (stock)，
按【店面】逐个产品给出：有没有货、在该店面有没有展示、图片。

数据来源（按优先级）
1. 环境变量 INSTOCK_STOCK_CSV / INSTOCK_DISPLAY_CSV
2. INSTOCK_DATA_DIR 目录下的 stock.csv / display.csv
3. INSTOCK_REGION（如 NZ/CA）→ Output-{region}/ 下的 stock/display 文件
4. 仓库自带 sample_data/（默认，开箱即用）

支持 CSV 和 Excel（.xlsx）；列名不需要完全一致，_pick 会做模糊匹配。
"""

import csv
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

try:
    from openpyxl import load_workbook
except ImportError:
    load_workbook = None

ROOT_DIR = Path(__file__).parent
SAMPLE_DIR = ROOT_DIR / "sample_data"
RUNNER_CONFIG_FILE = ROOT_DIR / "region_runner_config.json"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

CODE_KEYS = ["productcode", "product_code", "sku", "itemcode", "item_code",
             "code", "productid", "product_id", "product", "item"]
NAME_KEYS = ["productname", "product_name", "name", "description", "desc", "title"]
FAMILY_KEYS = ["family", "productfamily", "product_family", "category",
               "categoryname", "category_name", "group", "producttype", "type"]
STOCK_KEYS = ["stockqty", "stock_qty", "stock", "qty", "quantity", "onhand",
              "on_hand", "available", "availableqty", "available_qty", "soh"]
PRICE_KEYS = ["price", "unitprice", "unit_price", "sellprice", "sell_price",
              "saleprice", "sale_price", "retailprice", "retail_price"]
DISCONTINUE_KEYS = ["discontinued", "isdiscontinued", "is_discontinued",
                    "discontinue", "discontinueflag", "discontinue_flag", "status"]
STORE_KEYS = ["store", "storename", "store_name", "warehouse", "warehousename",
              "warehouse_name", "location", "branch", "shop", "displaywarehouse",
              "display_warehouse", "site"]
IMAGE_KEYS = ["imagefile", "image", "imageurl", "image_url", "img",
              "picture", "photo", "thumbnail", "thumb"]

ALL_STORES = "全部店面"
SAMPLE_REGION = "sample"


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
    text = str(value).strip().lower()
    if text in ("", "0", "false", "no", "n", "active", "live"):
        return False
    if text in ("1", "true", "yes", "y"):
        return True
    return "discontinue" in text


def _norm_code(value):
    return str(value).strip().upper() if value is not None else ""


def _read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_xlsx(path):
    if load_workbook is None:
        raise RuntimeError("读取 Excel 需要 openpyxl，请运行：pip install openpyxl")
    wb = load_workbook(path, read_only=True, data_only=True)
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


def _read_table(path):
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        return _read_xlsx(path)
    return _read_csv(path)


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
    """在目录里按候选文件名找 stock/display（优先 xlsx，其次 csv）。"""
    base = Path(directory)
    if not base.is_dir():
        return None
    for stem in stems:
        for ext in (".xlsx", ".csv"):
            candidate = base / f"{stem}{ext}"
            if candidate.is_file():
                return candidate
    return None


def _region_stock_stems(region_key):
    rk = region_key.upper()
    return ["stock", f"{rk}_stock", "product_stock_price", f"{rk}_product_stock_price"]


def _region_display_stems(region_key):
    rk = region_key.upper()
    return ["display", f"{rk}_display", "store_display", f"{rk}_store_display"]


def _load_runner_regions():
    if not RUNNER_CONFIG_FILE.exists():
        return {}
    try:
        with RUNNER_CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("regions", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


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
    if region_key and region_key != SAMPLE_REGION.upper():
        d = _region_output_dir(region_key)
        stock_path = _find_data_file(d, _region_stock_stems(region_key))
        display_path = _find_data_file(d, _region_display_stems(region_key))
        if stock_path is None:
            stock_path = d / "stock.xlsx"
        if display_path is None:
            display_path = d / "display.xlsx"
        return stock_path, display_path, f"region-{region_key}", d
    return SAMPLE_DIR / "stock.csv", SAMPLE_DIR / "display.csv", "sample", SAMPLE_DIR


def list_regions():
    options = [{"key": SAMPLE_REGION, "label": "示例数据", "has_latest": True}]
    for key, cfg in _load_runner_regions().items():
        out_dir = _region_output_dir(key)
        has_data = _find_data_file(out_dir, _region_stock_stems(key)) is not None
        options.append({
            "key": key,
            "label": cfg.get("label", key),
            "has_latest": has_data,
        })
    return options


def _resolve_image(raw, code, data_dir):
    """把图片列的值解析成可用路径/URL；也支持按产品编码自动找本地图。"""
    if raw:
        text = str(raw).strip()
        if text.lower().startswith(("http://", "https://")):
            return normalize_url(text)
        p = Path(text)
        if not p.is_absolute():
            p = ROOT_DIR / p
        if p.is_file():
            return str(p)

    search_dirs = [
        ROOT_DIR / "sample_images",
        Path(data_dir) / "images",
        SAMPLE_DIR / "images",
    ]
    for base in search_dirs:
        if not base.is_dir():
            continue
        for stem in (str(code).strip(), str(code).strip().upper(), str(code).strip().lower()):
            for ext in IMAGE_EXTS:
                candidate = base / f"{stem}{ext}"
                if candidate.is_file():
                    return str(candidate)
    return None


def _load_stock(rows, data_dir):
    out = []
    for row in rows:
        code = _pick(row, CODE_KEYS)
        if not code:
            continue
        qty = _to_float(_pick(row, STOCK_KEYS)) or 0.0
        out.append({
            "code": str(code).strip(),
            "name": str(_pick(row, NAME_KEYS) or "").strip(),
            "family": str(_pick(row, FAMILY_KEYS) or "未分类").strip(),
            "stock_qty": qty,
            "price": _to_float(_pick(row, PRICE_KEYS)),
            "discontinued": _is_discontinued(_pick(row, DISCONTINUE_KEYS)),
            "in_stock": qty > 0,
            "image": _resolve_image(_pick(row, IMAGE_KEYS), code, data_dir),
        })
    return out


def _load_display(rows):
    """返回 {store_name: set(codes)}。"""
    by_store = {}
    for row in rows:
        code = _pick(row, CODE_KEYS)
        if not code:
            continue
        store = _pick(row, STORE_KEYS)
        store = str(store).strip() if store else "（未标注店面）"
        by_store.setdefault(store, set()).add(_norm_code(code))
    return by_store


def list_stores(region=None):
    _, display_path, _, _ = resolve_sources(region)
    by_store = _load_display(_read_table(display_path))
    return [ALL_STORES] + sorted(by_store.keys())


def build_products(store=None, only_gap=False, include_discontinued=False, region=None):
    """核心：按店面逐个产品计算 有货/展示 状态与汇总指标。"""
    stock_path, display_path, source, data_dir = resolve_sources(region)
    if not Path(stock_path).is_file():
        raise FileNotFoundError(stock_path)
    if not Path(display_path).is_file():
        raise FileNotFoundError(display_path)
    stock_rows = _load_stock(_read_table(stock_path), data_dir)
    by_store = _load_display(_read_table(display_path))

    stores = [ALL_STORES] + sorted(by_store.keys())
    if store is None:
        store = ALL_STORES

    if store == ALL_STORES:
        displayed_codes = set().union(*by_store.values()) if by_store else set()
    else:
        displayed_codes = by_store.get(store, set())

    products = []
    for p in stock_rows:
        displayed = _norm_code(p["code"]) in displayed_codes
        gap = p["in_stock"] and (not p["discontinued"]) and (not displayed)
        item = dict(p)
        item["displayed"] = displayed
        item["gap"] = gap
        products.append(item)

    non_discontinue = [p for p in products if not p["discontinued"]]
    in_stock = [p for p in non_discontinue if p["in_stock"]]
    displayed_in_stock = [p for p in in_stock if p["displayed"]]
    not_displayed = [p for p in in_stock if not p["displayed"]]

    total_nd = len(non_discontinue)
    in_stock_n = len(in_stock)
    summary = {
        "store": store,
        "region": region or SAMPLE_REGION,
        "total_non_discontinue": total_nd,
        "in_stock_count": in_stock_n,
        "in_stock_rate": round(in_stock_n / total_nd * 100, 2) if total_nd else None,
        "displayed_in_stock_count": len(displayed_in_stock),
        "display_coverage_rate": round(len(displayed_in_stock) / in_stock_n * 100, 2) if in_stock_n else None,
        "not_displayed_count": len(not_displayed),
    }

    view = products
    if not include_discontinued:
        view = [p for p in view if not p["discontinued"]]
    if only_gap:
        view = [p for p in view if p["gap"]]
    view.sort(key=lambda p: (not p["gap"], not p["in_stock"], -p["stock_qty"], p["code"]))

    return {
        "source": source,
        "region": region or SAMPLE_REGION,
        "stock_path": str(stock_path),
        "display_path": str(display_path),
        "stores": stores,
        "selected_store": store,
        "summary": summary,
        "products": view,
        "regions": list_regions(),
    }


if __name__ == "__main__":
    import json
    data = build_products(store="Walls Road")
    print("stores:", data["stores"])
    print("summary:", json.dumps(data["summary"], ensure_ascii=False))
    for p in data["products"]:
        print(f"  {p['code']:10} 有货={'Y' if p['in_stock'] else 'N'}({int(p['stock_qty'])})"
              f" 展示={'Y' if p['displayed'] else 'N'} gap={'*' if p['gap'] else ' '} {p['name']}")
