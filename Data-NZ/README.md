# Data-NZ SQL 模板目录

把你的 .sql / .txt 查询文件放这里，执行器会按文件名依次运行。

## 输出文件名规则

| 模板文件名 | 输出到 Output-NZ/ |
|-----------|-------------------|
| product_stock_price.sql | stock.xlsx / stock.csv |
| **display_with_families.sql** | **display.xlsx / display.csv**（店面下拉靠这个） |
| weekly_sales.sql | weekly_sales.xlsx |

## 有货未展示看板需要哪些文件？

**只要 Excel 就够了**（`stock.xlsx` + `display.xlsx`）。

- 看板会**优先读 .xlsx**，没有 xlsx 才读 csv
- SQL 导出器会同时生成 csv + xlsx，**csv 可以留着备用**（给其他工具用），看板不读也没关系
- `weekly_sales` 是给别的分析用的，**有货未展示看板不需要**

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
