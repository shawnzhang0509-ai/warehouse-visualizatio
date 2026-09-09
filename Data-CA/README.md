# Data-CA SQL 模板目录

加拿大地区的 SQL / TXT 查询放这里，执行器（`app.py`）会按文件名依次运行，导出到 `Output-CA/`。

## 支持 .sql 和 .txt

两种后缀等价，可混用。例如：

| 文件 | 输出 |
|------|------|
| `product_stock_price.sql` | `Output-CA/stock.csv` |
| `stock_discontinued.sql` | `Output-CA/stock_discontinued.csv` |
| `display.txt` | `Output-CA/display.csv` |
| `weekly_sales.txt` | `Output-CA/weekly_sales.csv` |

若同一输出同时有 `.sql` 和 `.txt`（如 `display.sql` + `display.txt`），**优先执行 .sql**。

占位模板（`SELECT GETDATE()`）和以 `example_` 开头的文件会自动跳过。

## 看板使用

1. 在 `app.py` 执行界面勾选 **CA 加拿大**，跑完 SQL
2. 打开 `panel_app.py`（有货未展示看板），地区下拉选 **CA 加拿大**
3. 若显示「尚无数据」，说明 `Output-CA/stock.csv` 还未生成，先执行 SQL

## 黑名单（可选）

复制 `Data-NZ/blacklist.example.csv` 到 `Output-CA/blacklist.csv`，按需增删 SKU。
