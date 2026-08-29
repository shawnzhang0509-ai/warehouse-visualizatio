"""看板：有货未展示（in-stock but not displayed）分析面板。

核心逻辑
--------
用"店面展示"数据 (display) 比对"non-discontinue 且有货"的产品 (stock)，
找出：有货、未停产 (non-discontinue)、但当前没有在店面展示的产品。

同时给出两个关键指标：
- 店铺有货率  = 有货的未停产产品数 / 全部未停产产品数
- 展示覆盖率  = 已展示且有货的未停产产品数 / 有货的未停产产品数

数据来源（按优先级）
--------------------
1. 环境变量 INSTOCK_STOCK_CSV / INSTOCK_DISPLAY_CSV 指定的 CSV 文件；
2. 环境变量 INSTOCK_DATA_DIR 目录下的 stock.csv / display.csv；
3. 环境变量 INSTOCK_REGION（如 NZ/CA）指向 Output-{region}/latest/；
4. 仓库自带的 sample_data/ 示例数据（默认，保证开箱即用）。

CSV 可以直接用 Data-NZ 里的 SQL（stock.txt / display.txt）
跑出来的导出结果——列名不需要完全一致，下面的 _pick 会做模糊匹配。
"""

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

app = Flask(__name__)

ROOT_DIR = Path(__file__).parent
SAMPLE_DIR = ROOT_DIR / "sample_data"
RUNNER_CONFIG_FILE = ROOT_DIR / "region_runner_config.json"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")

# 列名模糊匹配候选（全部转小写后比较）。真实 SQL 的列名可放进来。
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
IMAGE_KEYS = ["imageurl", "image_url", "thumbnail", "thumbnailurl", "thumbnail_url",
              "photo", "picture", "img", "image", "productimage", "product_image"]
WAREHOUSE_KEYS = ["displaywarehouse", "display_warehouse", "warehouse", "store",
                  "storename", "store_name", "shop", "location", "branch"]


def _utc_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _pick(row, keys):
    """从一行 dict 中按候选键（大小写/下划线不敏感）取第一个非空值。"""
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
    """把各种"停产"表示统一成布尔：1/true/yes/y/discontinued 视为停产。"""
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in ("", "0", "false", "no", "n", "active", "live"):
        return False
    if text in ("1", "true", "yes", "y"):
        return True
    return "discontinue" in text


def _read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


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


def _resolve_sources(region=None):
    """确定 stock / display 两个数据源路径与来源类型。"""
    stock_env = os.getenv("INSTOCK_STOCK_CSV")
    display_env = os.getenv("INSTOCK_DISPLAY_CSV")
    data_dir = os.getenv("INSTOCK_DATA_DIR")
    region_env = (region or os.getenv("INSTOCK_REGION") or "").strip().upper()

    if stock_env and display_env:
        stock_path = Path(stock_env)
        display_path = Path(display_env)
        return stock_path, display_path, "csv", stock_path.parent
    if data_dir:
        d = Path(data_dir)
        return d / "stock.csv", d / "display.csv", "csv", d
    if region_env:
        d = _region_output_dir(region_env) / "latest"
        return d / "stock.csv", d / "display.csv", f"region-{region_env}", d
    return SAMPLE_DIR / "stock.csv", SAMPLE_DIR / "display.csv", "sample", SAMPLE_DIR


def _norm_code(value):
    return str(value).strip().upper() if value is not None else ""


def _norm_warehouse(value):
    return str(value).strip() if value is not None else ""


def _find_local_image(data_dir, code):
    images_dir = Path(data_dir) / "images"
    if not images_dir.is_dir():
        return None
    stem = str(code).strip()
    for ext in IMAGE_EXTS:
        candidate = images_dir / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
        candidate = images_dir / f"{stem.upper()}{ext}"
        if candidate.is_file():
            return candidate
        candidate = images_dir / f"{stem.lower()}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _image_url_for_product(code, image_url_from_csv, data_dir):
    if image_url_from_csv:
        url = str(image_url_from_csv).strip()
        if url.startswith(("http://", "https://", "/")):
            return url
    local = _find_local_image(data_dir, code)
    if local:
        return f"/api/product-image/{code}"
    return ""


def _load_stock(rows, data_dir):
    out = []
    for row in rows:
        code = _pick(row, CODE_KEYS)
        if not code:
            continue
        image_raw = _pick(row, IMAGE_KEYS)
        out.append({
            "code": str(code).strip(),
            "name": str(_pick(row, NAME_KEYS) or "").strip(),
            "family": str(_pick(row, FAMILY_KEYS) or "未分类").strip(),
            "stock_qty": _to_float(_pick(row, STOCK_KEYS)) or 0.0,
            "price": _to_float(_pick(row, PRICE_KEYS)),
            "discontinued": _is_discontinued(_pick(row, DISCONTINUE_KEYS)),
            "image_url": _image_url_for_product(code, image_raw, data_dir),
        })
    return out


def _load_display_map(rows):
    """返回 warehouse -> set(codes) 以及全部 warehouse 列表。"""
    by_warehouse = {}
    all_codes = set()
    for row in rows:
        code = _pick(row, CODE_KEYS)
        if not code:
            continue
        norm = _norm_code(code)
        all_codes.add(norm)
        warehouse = _norm_warehouse(_pick(row, WAREHOUSE_KEYS) or "全部店面")
        by_warehouse.setdefault(warehouse, set()).add(norm)
    return by_warehouse, sorted(by_warehouse.keys()), all_codes


def compute_gap(region=None, warehouse=None):
    stock_path, display_path, source, data_dir = _resolve_sources(region)
    diagnostics = []

    stock_rows = _load_stock(_read_csv(stock_path), data_dir)
    display_by_wh, warehouses, all_display_codes = _load_display_map(_read_csv(display_path))

    selected_wh = _norm_warehouse(warehouse) if warehouse else ""
    if selected_wh and selected_wh in display_by_wh:
        display_codes = display_by_wh[selected_wh]
    elif selected_wh:
        display_codes = set()
        diagnostics.append({
            "level": "warning",
            "message": f"店面「{selected_wh}」在展示数据中无记录，展示清单视为空。",
        })
    else:
        display_codes = all_display_codes

    non_discontinue = [p for p in stock_rows if not p["discontinued"]]
    in_stock = [p for p in non_discontinue if p["stock_qty"] > 0]

    displayed_in_stock = [p for p in in_stock if _norm_code(p["code"]) in display_codes]
    not_displayed = [p for p in in_stock if _norm_code(p["code"]) not in display_codes]
    not_displayed.sort(key=lambda p: p["stock_qty"], reverse=True)

    total_nd = len(non_discontinue)
    in_stock_n = len(in_stock)
    in_stock_rate = round(in_stock_n / total_nd * 100, 2) if total_nd else None
    coverage = round(len(displayed_in_stock) / in_stock_n * 100, 2) if in_stock_n else None

    fam = {}
    for p in in_stock:
        b = fam.setdefault(p["family"], {"in_stock": 0, "not_displayed": 0})
        b["in_stock"] += 1
    for p in not_displayed:
        fam[p["family"]]["not_displayed"] += 1
    by_family = [
        {"family": k, "in_stock_count": v["in_stock"], "not_displayed_count": v["not_displayed"]}
        for k, v in sorted(fam.items(), key=lambda kv: kv[1]["not_displayed"], reverse=True)
    ]

    products = []
    for p in in_stock:
        displayed = _norm_code(p["code"]) in display_codes
        products.append({
            "code": p["code"],
            "name": p["name"],
            "family": p["family"],
            "stock_qty": round(p["stock_qty"], 2),
            "price": p["price"],
            "image_url": p["image_url"],
            "displayed": displayed,
            "gap": not displayed,
        })
    products.sort(key=lambda p: (not p["gap"], -p["stock_qty"]))

    if not stock_rows:
        diagnostics.append({"level": "warning", "message": f"库存数据为空：{stock_path}"})
    if not all_display_codes:
        diagnostics.append({"level": "warning", "message": f"展示数据为空：{display_path}"})

    region_options = [{"key": "sample", "label": "示例数据"}]
    for key, cfg in _load_runner_regions().items():
        output_dir = _region_output_dir(key)
        latest = output_dir / "latest"
        region_options.append({
            "key": key,
            "label": cfg.get("label", key),
            "output_dir": str(output_dir),
            "has_latest": latest.is_dir() and (latest / "stock.csv").is_file(),
        })

    return {
        "status": "success",
        "source": source,
        "region": (region or os.getenv("INSTOCK_REGION") or "sample").upper() if source.startswith("region") else "sample",
        "warehouse": selected_wh or None,
        "warehouses": warehouses,
        "regionOptions": region_options,
        "asOf": _utc_iso(),
        "sources": {"stock": str(stock_path), "display": str(display_path), "data_dir": str(data_dir)},
        "summary": {
            "total_non_discontinue": total_nd,
            "in_stock_count": in_stock_n,
            "in_stock_rate": in_stock_rate,
            "displayed_in_stock_count": len(displayed_in_stock),
            "display_coverage_rate": coverage,
            "not_displayed_count": len(not_displayed),
        },
        "products": products,
        "not_displayed": [
            {
                "code": p["code"],
                "name": p["name"],
                "family": p["family"],
                "stock_qty": round(p["stock_qty"], 2),
                "price": p["price"],
                "image_url": p["image_url"],
            }
            for p in not_displayed
        ],
        "by_family": by_family,
        "diagnostics": diagnostics,
    }


@app.route("/")
def index():
    return send_file(ROOT_DIR / "panel.html")


@app.route("/echarts.min.js")
def echarts_lib():
    return send_from_directory(ROOT_DIR, "echarts.min.js")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "time": _utc_iso()})


@app.route("/api/gap")
def api_gap():
    try:
        region = request.args.get("region", "").strip() or None
        warehouse = request.args.get("warehouse", "").strip() or None
        return jsonify(compute_gap(region=region, warehouse=warehouse))
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": f"找不到数据文件：{exc}"}), 200
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 200


@app.route("/api/product-image/<code>")
def product_image(code):
    region = request.args.get("region", "").strip() or None
    _, _, _, data_dir = _resolve_sources(region)
    local = _find_local_image(data_dir, code)
    if local:
        return send_file(local)
    return jsonify({"status": "error", "message": "图片不存在"}), 404


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
