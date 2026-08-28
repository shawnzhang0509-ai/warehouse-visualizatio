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
3. 仓库自带的 sample_data/ 示例数据（默认，保证开箱即用）。

CSV 可以直接用 Data-NZ 里的 SQL（product_stock_price.sql / display.sql）
跑出来的导出结果——列名不需要完全一致，下面的 _pick 会做模糊匹配。
"""

import csv
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, send_file, send_from_directory

app = Flask(__name__)

ROOT_DIR = Path(__file__).parent
SAMPLE_DIR = ROOT_DIR / "sample_data"

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
    # 状态字符串里带 discontinue 视为停产
    return "discontinue" in text


def _read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _resolve_sources():
    """确定 stock / display 两个数据源路径与来源类型。"""
    stock_env = os.getenv("INSTOCK_STOCK_CSV")
    display_env = os.getenv("INSTOCK_DISPLAY_CSV")
    data_dir = os.getenv("INSTOCK_DATA_DIR")

    if stock_env and display_env:
        return Path(stock_env), Path(display_env), "csv"
    if data_dir:
        d = Path(data_dir)
        return d / "stock.csv", d / "display.csv", "csv"
    return SAMPLE_DIR / "stock.csv", SAMPLE_DIR / "display.csv", "sample"


def _norm_code(value):
    return str(value).strip().upper() if value is not None else ""


def _load_stock(rows):
    out = []
    for row in rows:
        code = _pick(row, CODE_KEYS)
        if not code:
            continue
        out.append({
            "code": str(code).strip(),
            "name": str(_pick(row, NAME_KEYS) or "").strip(),
            "family": str(_pick(row, FAMILY_KEYS) or "未分类").strip(),
            "stock_qty": _to_float(_pick(row, STOCK_KEYS)) or 0.0,
            "price": _to_float(_pick(row, PRICE_KEYS)),
            "discontinued": _is_discontinued(_pick(row, DISCONTINUE_KEYS)),
        })
    return out


def _load_display_codes(rows):
    codes = set()
    for row in rows:
        code = _pick(row, CODE_KEYS)
        if code:
            codes.add(_norm_code(code))
    return codes


def compute_gap():
    stock_path, display_path, source = _resolve_sources()
    diagnostics = []

    stock_rows = _load_stock(_read_csv(stock_path))
    display_codes = _load_display_codes(_read_csv(display_path))

    non_discontinue = [p for p in stock_rows if not p["discontinued"]]
    in_stock = [p for p in non_discontinue if p["stock_qty"] > 0]

    displayed_in_stock = [p for p in in_stock if _norm_code(p["code"]) in display_codes]
    not_displayed = [p for p in in_stock if _norm_code(p["code"]) not in display_codes]
    not_displayed.sort(key=lambda p: p["stock_qty"], reverse=True)

    total_nd = len(non_discontinue)
    in_stock_n = len(in_stock)
    in_stock_rate = round(in_stock_n / total_nd * 100, 2) if total_nd else None
    coverage = round(len(displayed_in_stock) / in_stock_n * 100, 2) if in_stock_n else None

    # 按产品系列聚合
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

    if not stock_rows:
        diagnostics.append({"level": "warning", "message": f"库存数据为空：{stock_path}"})
    if not display_codes:
        diagnostics.append({"level": "warning", "message": f"展示数据为空：{display_path}"})

    return {
        "status": "success",
        "source": source,
        "asOf": _utc_iso(),
        "sources": {"stock": str(stock_path), "display": str(display_path)},
        "summary": {
            "total_non_discontinue": total_nd,
            "in_stock_count": in_stock_n,
            "in_stock_rate": in_stock_rate,
            "displayed_in_stock_count": len(displayed_in_stock),
            "display_coverage_rate": coverage,
            "not_displayed_count": len(not_displayed),
        },
        "not_displayed": [
            {
                "code": p["code"],
                "name": p["name"],
                "family": p["family"],
                "stock_qty": round(p["stock_qty"], 2),
                "price": p["price"],
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
        return jsonify(compute_gap())
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": f"找不到数据文件：{exc}"}), 200
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 200


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
