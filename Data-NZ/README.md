# Data-NZ SQL 模板目录

把你的 .sql / .txt 查询文件放这里，执行器会按文件名依次运行。

## 输出文件名规则

| 模板文件名 | 输出到 Output-NZ/ |
|-----------|-------------------|
| product_stock_price.sql | stock.xlsx / stock.csv |
| **display_with_families.sql** | **display.xlsx / display.csv**（店面下拉靠这个） |
| weekly_sales.sql | weekly_sales.xlsx |

## 有货未展示看板需要哪些文件？

**stock + display 各一份即可**（看板**优先读 .csv**，比 .xlsx 快很多；同目录两者都有时用 csv）。

| 文件 | 是否需要 |
|------|---------|
| `stock.csv` 或 `stock.xlsx` | ✅ 必须（推荐 csv） |
| `display.csv` 或 `display.xlsx` | ✅ 必须 |
| `blacklist.xlsx` | 可选 |
| `weekly_sales` | 看板不需要 |

慢的主要原因不是 Excel「格式错了」，而是：
1. **行数多**（在产 ~2500，含停产 ~1.6 万）
2. **xlsx 解析**比 csv 慢（openpyxl 逐行读 XML）
3. 以前启动时会对**每个 SKU 扫描 images 目录**找图（v1.5 已改为按需加载）

## 黑名单（可选）

在 `Output-NZ/` 下放 `blacklist.xlsx`（或 `blacklist.csv`），一列 SKU 即可：

| sku |
|-----|
| 999-989 |
| 810-004 |

- 列名支持：`sku`、`SKU`、`编码`、`ProductCode` 等
- 黑名单里的 SKU **不会出现在看板产品列表**，也不参与统计
- 修改黑名单后，看板点「刷新数据」或重启即可生效
- 没有黑名单文件时，行为与之前完全一致

## 注意

- 不要放 `stock.txt` 这类占位文件，会和 `product_stock_price.sql` 抢同一个 stock.xlsx 导致数据被覆盖。
- 以 `example_` 开头的文件会自动跳过，不执行。
