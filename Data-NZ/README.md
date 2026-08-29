# Data-NZ SQL 模板目录

把你的 .sql / .txt 查询文件放这里，执行器会按文件名依次运行。

## 输出文件名规则

| 模板文件名 | 输出到 Output-NZ/ |
|-----------|-------------------|
| product_stock_price.sql | stock.xlsx / stock.csv |
| display.sql | display.xlsx / display.csv |
| weekly_sales.sql | weekly_sales.xlsx |
| 其他文件名 | 同名.xlsx（如 my_report.sql → my_report.xlsx）|

## 注意

- 不要放 `stock.txt` 这类占位文件，会和 `product_stock_price.sql` 抢同一个 stock.xlsx 导致数据被覆盖。
- 以 `example_` 开头的文件会自动跳过，不执行。
