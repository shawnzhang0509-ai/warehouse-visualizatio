# Data-NZ SQL 模板目录

把你的 `.sql` / `.txt` 查询文件放这里，执行器会按文件名依次运行（两种后缀可混用；同一输出若 .sql 与 .txt 并存，优先 .sql）。

## 输出文件名规则

| 模板文件名 | 输出到 Output-NZ/ |
|-----------|-------------------|
| **product_stock_price.sql** | **stock.csv**（仅在产，`IsDiscontinued = 0`） |
| **stock_discontinued.sql** | **stock_discontinued.csv**（仅停产，`IsDiscontinued = 1`） |
| display_with_families.sql / display.sql | display.csv（**陈列区** `%Display%`） |
| **storage.sql** | **storage.csv**（**店面后仓** `%Storage%`，勿覆盖 display） |
| weekly_sales.sql | weekly_sales.csv |

**两个 stock 模板请一起执行（两库）。** 看板启动时会读 `stock.csv` + `stock_discontinued.csv`，状态栏会显示 `stock.csv + stock_discontinued.csv（两库）`。只导出一个文件时，停产=「全部」会缺数据。

**产品图** 依赖 `ImageUrl` 列（SQL 已含）。若看板提示「无 ImageUrl」，请用本目录最新 `product_stock_price.sql` / `stock_discontinued.sql` 重新导出。

**仅导出 CSV，不再生成 .xlsx。** 重新执行 SQL 时会自动删除同名的旧版 Excel 文件。

## 有货未展示看板需要哪些文件？

| 文件 | 是否需要 |
|------|---------|
| `stock.csv` | ✅ 必须（在产，由 product_stock_price.sql 导出） |
| `stock_discontinued.csv` | ✅ 必须（停产，由 stock_discontinued.sql 导出） |
| `display.csv` | ✅ 必须（陈列区 `%Display%`） |
| `storage.csv` | ✅ 推荐（店面后仓 `%Storage%`，看板「仓有·店仓无 / 双有未陈列」） |
| `blacklist.csv` | 可选（见 `blacklist.example.csv` 模板） |
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

## 三层库存逻辑（v1.7.5+）

| 层级 | 文件 | SQL 条件 | 看板含义 |
|------|------|----------|----------|
| 中心仓 | stock.csv | Carbine / Walls / GC | 有货 |
| 店后仓 | storage.csv | `%Storage%` | 店面 Storage 有货 |
| 陈列区 | display.csv | `%Display%` | 已陈列 |

状态示例：
- **仓有·店仓无**：中心仓有货，店后仓没有，也未陈列 → 可能要调拨
- **双有未陈列**：中心仓 + 店后仓都有货，但未陈列 → 比 display 更严格的待处理

`display.sql` 与 `storage.sql` **各导各的**，不要把 Display 改成 Storage 覆盖。

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

复制 `Data-NZ/blacklist.example.csv` 到 `Output-NZ/blacklist.csv`，或直接在 `Output-NZ/` 新建 `blacklist.csv` / `blacklist.xlsx`，第一列 SKU：

| sku | note（可选） |
|-----|-------------|
| 999-989 | Dummy Product |

- 看板状态栏应显示「黑名单：已排除 N 个 SKU」；若显示 **0 个 / 未找到文件**，说明路径不对或尚未点「刷新数据」
- 黑名单对**所有店面**生效（Onehunga、Westgate、CHCH 等都会排除）
- 修改后必须点「刷新数据」

## 渠道负责人（可选，负责人报表）

复制 `Data-NZ/channel_owners.example.csv` 到 `Output-NZ/channel_owners.csv`。

| 列 | 说明 |
|----|------|
| owner / 负责人 | kaya、Andy、Juli 等 |
| channel / 渠道 | SKU 前三位或区间（如 `271`、`155-319`） |
| lead_time | Lead Time 天数（报表展示） |
| merge_products / 必须合并计算的产品 | `所有` 或 `Calton+Arden+Bexley`（+ 分隔，合并为 1 个统计单位） |
| merge_regions | 如 `南北岛`（备注） |
| note / 拆分原因 | 可选备注 |

看板 **「负责人报表」** 标签页：按负责人汇总有货率，可导出 `owner_report_*.csv` 定期报表。  
**「SKU前三位汇总」** 表会增加 **负责人** 列。

## 注意

- 不要放 `stock.txt` 这类占位文件，会和 `product_stock_price.sql` 抢同一个 stock.csv 导致数据被覆盖。
- 以 `example_` 开头的文件会自动跳过，不执行。
