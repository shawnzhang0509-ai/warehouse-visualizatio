"""有货未展示看板 —— 纯本地数据层（不依赖任何网页/Flask）。

核心：用店面展示数据 (display) 比对 non-discontinue 且有货的产品 (stock)，
按【店面】逐个产品给出：有没有货、在该店面有没有展示、图片。

数据来源（按优先级）
1. 环境变量 INSTOCK_STOCK_CSV / INSTOCK_DISPLAY_CSV
2. INSTOCK_DATA_DIR 目录下的 stock.csv / display.csv
3. 仓库自带 sample_data/（默认，开箱即用）

列名不需要完全一致，_pick 会做模糊匹配；display 里需要一个"店面/仓库"列。
"""

import csv
import os
from pathlib import Path

ROOT_DIR = Path(__file__).parent
SAMPLE_DIR = ROOT_DIR / "sample_data"

CODE_KEYS = ["productcode", "product_code", "sku", "itemcode", "item_code",
             "code", "productid", "product_id", "product", "item"]
NAME_KEYS = ["productname", "product_name", "name", "description", "desc", "title"]
FAMILY_KEYS = ["family", "productfamily", "product_family", "category",
               "categoryname", "category_name", "group", "producttype", "type"]
STOCK_KEYS = ["stockqty", "stock_qty", "stock", "qty", "quantity", "onhand",
              "on_hand", "available", "availableqty", "available_qty", "soh"]
PRICE_KEYS = ["price", "unitprice", "unit_price", "sellprice", "sell_price",
              "retailprice", "retail_price"]
DISCONTINUE_KEYS = ["discontinued", "isdiscontinued", "is_discontinued",
                    "discontinue", "discontinueflag", "discontinue_flag", "status"]
STORE_KEYS = ["store", "storename", "store_name", "warehouse", "warehousename",
              "warehouse_name", "location", "branch", "shop", "displaywarehouse",
              "display_warehouse", "site"]
IMAGE_KEYS = ["imagefile", "image", "imageurl", "image_url", "img",
              "picture", "photo", "thumbnail", "thumb"]

ALL_STORES = "全部店面"


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


def resolve_sources():
    stock_env = os.getenv("INSTOCK_STOCK_CSV")
    display_env = os.getenv("INSTOCK_DISPLAY_CSV")
    data_dir = os.getenv("INSTOCK_DATA_DIR")
    if stock_env and display_env:
        return Path(stock_env), Path(display_env), "csv"
    if data_dir:
        d = Path(data_dir)
        return d / "stock.csv", d / "display.csv", "csv"
    return SAMPLE_DIR / "stock.csv", SAMPLE_DIR / "display.csv", "sample"


def _resolve_image(raw):
    """把图片列的值解析成可用路径/URL；相对路径按仓库根目录解析。"""
    if not raw:
        return None
    text = str(raw).strip()
    if text.lower().startswith(("http://", "https://")):
        return text
    p = Path(text)
    if not p.is_absolute():
        p = ROOT_DIR / p
    return str(p)


def _load_stock(rows):
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
            "image": _resolve_image(_pick(row, IMAGE_KEYS)),
        })
    return out


def _load_display(rows):
    """返回 {store_name: set(codes)}；同时汇总一个 ALL_STORES 的全集。"""
    by_store = {}
    for row in rows:
        code = _pick(row, CODE_KEYS)
        if not code:
            continue
        store = _pick(row, STORE_KEYS)
        store = str(store).strip() if store else "（未标注店面）"
        by_store.setdefault(store, set()).add(_norm_code(code))
    return by_store


def list_stores():
    _, display_path, _ = resolve_sources()
    by_store = _load_display(_read_csv(display_path))
    return [ALL_STORES] + sorted(by_store.keys())


def build_products(store=None, only_gap=False, include_discontinued=False):
    """核心：按店面逐个产品计算 有货/展示 状态与汇总指标。"""
    stock_path, display_path, source = resolve_sources()
    stock_rows = _load_stock(_read_csv(stock_path))
    by_store = _load_display(_read_csv(display_path))

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

    # 汇总只统计未停产产品
    non_discontinue = [p for p in products if not p["discontinued"]]
    in_stock = [p for p in non_discontinue if p["in_stock"]]
    displayed_in_stock = [p for p in in_stock if p["displayed"]]
    not_displayed = [p for p in in_stock if not p["displayed"]]

    total_nd = len(non_discontinue)
    in_stock_n = len(in_stock)
    summary = {
        "store": store,
        "total_non_discontinue": total_nd,
        "in_stock_count": in_stock_n,
        "in_stock_rate": round(in_stock_n / total_nd * 100, 2) if total_nd else None,
        "displayed_in_stock_count": len(displayed_in_stock),
        "display_coverage_rate": round(len(displayed_in_stock) / in_stock_n * 100, 2) if in_stock_n else None,
        "not_displayed_count": len(not_displayed),
    }

    # 列表过滤
    view = products
    if not include_discontinued:
        view = [p for p in view if not p["discontinued"]]
    if only_gap:
        view = [p for p in view if p["gap"]]
    # 排序：有货未展示优先，其次有货，再按库存降序
    view.sort(key=lambda p: (not p["gap"], not p["in_stock"], -p["stock_qty"], p["code"]))

    return {
        "source": source,
        "stock_path": str(stock_path),
        "display_path": str(display_path),
        "stores": stores,
        "selected_store": store,
        "summary": summary,
        "products": view,
    }


if __name__ == "__main__":
    import json
    data = build_products(store="Onehunga")
    print("stores:", data["stores"])
    print("summary:", json.dumps(data["summary"], ensure_ascii=False))
    for p in data["products"]:
        print(f"  {p['code']:10} 有货={'Y' if p['in_stock'] else 'N'}({int(p['stock_qty'])})"
              f" 展示={'Y' if p['displayed'] else 'N'} gap={'*' if p['gap'] else ' '} {p['name']}")
