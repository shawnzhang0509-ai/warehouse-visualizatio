# Data-NZ SQL 模板目录

把你的 .sql / .txt 查询文件放这里，执行器会按文件名依次运行。

## 输出文件名规则

| 模板文件名 | 输出到 Output-NZ/ |
|-----------|-------------------|
| product_stock_price.sql | **stock.csv** |
| **display_with_families.sql** | **display.csv**（店面下拉靠这个） |
| weekly_sales.sql | weekly_sales.csv |

**仅导出 CSV，不再生成 .xlsx。** 重新执行 SQL 时会自动删除同名的旧版 Excel 文件。

## 有货未展示看板需要哪些文件？

**stock + display 各一份 CSV 即可。**

| 文件 | 是否需要 |
|------|---------|
| `stock.csv` | ✅ 必须 |
| `display.csv` | ✅ 必须 |
| `blacklist.csv` | 可选 |
| `weekly_sales.csv` | 看板不需要 |

## 黑名单（可选）

在 `Output-NZ/` 下放 `blacklist.csv`，一列 SKU 即可：

| sku |
|-----|
| 999-989 |
| 810-004 |

- 列名支持：`sku`、`SKU`、`编码`、`ProductCode` 等
- 黑名单里的 SKU **不会出现在看板产品列表**，也不参与统计
- 修改黑名单后，看板点「刷新数据」或重启即可生效
- 没有黑名单文件时，行为与之前完全一致

## 注意

- 不要放 `stock.txt` 这类占位文件，会和 `product_stock_price.sql` 抢同一个 stock.csv 导致数据被覆盖。
- 以 `example_` 开头的文件会自动跳过，不执行。
- 若 Output 目录里还有旧的 `stock.xlsx` / `display.xlsx`，请重新跑一次 SQL 导出（会自动删掉 xlsx），或手动删除。
