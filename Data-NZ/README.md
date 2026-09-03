# Data-NZ SQL 模板目录

把你的 .sql / .txt 查询文件放这里，执行器会按文件名依次运行。

## 输出文件名规则

| 模板文件名 | 输出到 Output-NZ/ |
|-----------|-------------------|
| **product_stock_price.sql** | **stock.csv**（仅在产，`IsDiscontinued = 0`） |
| **stock_discontinued.sql** | **stock_discontinued.csv**（仅停产，`IsDiscontinued = 1`） |
| display_with_families.sql | display.csv（店面下拉靠这个） |
| weekly_sales.sql | weekly_sales.csv |

**两个 stock 模板请一起执行。** 看板启动时会读 `stock.csv` + `stock_discontinued.csv`，比从一个 1.6 万行大文件里筛停产快得多。

**仅导出 CSV，不再生成 .xlsx。** 重新执行 SQL 时会自动删除同名的旧版 Excel 文件。

## 有货未展示看板需要哪些文件？

| 文件 | 是否需要 |
|------|---------|
| `stock.csv` | ✅ 必须（在产，由 product_stock_price.sql 导出） |
| `stock_discontinued.csv` | ✅ 必须（停产，由 stock_discontinued.sql 导出） |
| `display.csv` | ✅ 必须 |
| `blacklist.csv` | 可选 |
| `weekly_sales.csv` | 看板不需要 |

## 看板默认行为（v1.5.5+）

- **默认停产筛选 =「全部」**（在产 + 停产）
- **启动时预加载停产 SKU**（`PANEL_EAGER_DISCONTINUED=1`，默认开启）
- 切换「在产 / 全部 / 已停产」**只筛界面，不重新读文件**
- 若只需在产、要更快启动：设环境变量 `PANEL_EAGER_DISCONTINUED=0`

## 为什么不要把 Discontinued 写进 display？

**不会明显变快。** 原因：

1. **display 只有「已展示」的 SKU**（几百～几千行），而慢在 **stock 全量 1.6 万行**
2. **有货未展示**要看的是「没在 display 里」的款——它们本来就不在 display 表里
3. **停产标记**必须从 stock（主数据）来，display 覆盖不了「未展示 + 停产」的 SKU

## 推荐加速方案：拆分 stock 导出

在 SQL 里拆成两个查询（或两个模板文件）：

```sql
-- product_stock_price.sql  → stock.csv
-- WHERE Discontinued = 0  （或在产条件）

-- stock_discontinued.sql   → stock_discontinued.csv
-- WHERE Discontinued = 1
```

效果：

| 模式 | 读什么 | 速度 |
|------|--------|------|
| 停产=**在产**（默认） | 只读 stock.csv ~2500 行 | 快 |
| 停产=**全部/已停产** | stock.csv + stock_discontinued.csv | 比从一个大文件里筛停产快 |

若只有一个合并的 stock.csv，看板也能用，但切「全部」时要处理全部 1.6 万行。

## display.csv 列说明

| 列 | 说明 |
|----|------|
| WarehouseName / Store | 店面名（如 CHCH Display） |
| Sku / ProductCode | 产品编码 |
| ProductName | 名称（可选） |
| ProductFamily | 系列（可选，用于同组豁免） |
| DisplayQty | 展示数量（可选） |

**不需要**在 display 里加 Discontinued 列。

## 黑名单（可选）

在 `Output-NZ/` 下放 `blacklist.csv`，一列 SKU 即可。

## 注意

- 不要放 `stock.txt` 这类占位文件，会和 `product_stock_price.sql` 抢同一个 stock.csv 导致数据被覆盖。
- 以 `example_` 开头的文件会自动跳过，不执行。
