# Data-NZ SQL 模板目录

把你的 .sql / .txt 查询文件放这里，执行器会按文件名依次运行。

## 输出文件名规则

| 模板文件名 | 输出到 Output-NZ/ |
|-----------|-------------------|
| product_stock_price.sql | stock.xlsx / stock.csv |
| **display.sql** | **display.xlsx / display.csv**（店面下拉靠这个） |
| weekly_sales.sql | weekly_sales.xlsx |
| 其他文件名 | 同名.xlsx（如 my_report.sql → my_report.xlsx）|

## display 没数据 / 店面下拉为空？

1. 确认 `Data-NZ/display.sql` 存在（不是 display.sql.example）
2. 执行后打开 `Output-NZ/display.xlsx`，应有**很多行**，且有一列店面名（如 Walls Road、Onehunga）
3. `display.minimal.sql` **不会**写入 display.xlsx，需用标准文件名 `display.sql`
4. 若 display 只有 1 行，执行器日志会提示警告

## 注意

- 不要放 `stock.txt` 这类占位文件，会和 `product_stock_price.sql` 抢同一个 stock.xlsx 导致数据被覆盖。
- 以 `example_` 开头的文件会自动跳过，不执行。
