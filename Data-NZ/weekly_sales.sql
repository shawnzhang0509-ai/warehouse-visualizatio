-- Branch + ProductFamily 周级别销量 & 销售金额汇总（2026 年起自动延续）
-- 由 grab_sales.bat / scripts/grab_sales.py 执行，输出 data/weekly_sales.xlsx
-- 与 grab_display（Display 库存）独立，共用 grabber_config.json 数据库连接

DECLARE @StartDate DATE = '2026-01-01';
DECLARE @EndDate   DATE = CAST(GETDATE() AS DATE);

WITH WeeklySales AS (
    SELECT
        b.Name AS BranchName,
        p.ProductFamily,
        LEFT(p.Sku, 3) AS Channel,
        p.Sku,
        p.Name AS ProductName,
        CONCAT(
            YEAR(o.DateCreatedUtc),
            '-W',
            RIGHT('0' + CAST(DATEPART(WEEK, o.DateCreatedUtc) AS VARCHAR(2)), 2)
        ) AS YearWeek,
        CONVERT(VARCHAR(10), DATEADD(WEEK, DATEDIFF(WEEK, 0, o.DateCreatedUtc), 0), 23) AS WeekStart,
        CONVERT(VARCHAR(10), DATEADD(DAY, 6, DATEADD(WEEK, DATEDIFF(WEEK, 0, o.DateCreatedUtc), 0)), 23) AS WeekEnd,
        SUM(ol.Quantity) AS TotalQty,
        SUM(ol.Quantity * ISNULL(ol.NormalUnitSalePrice - ISNULL(ol.Discount, 0), 0)) AS TotalAmount,
        COUNT(DISTINCT o.Id) AS OrderCount
    FROM Orders o
    JOIN OrderLines ol ON ol.OrderId = o.Id
    JOIN Products p ON ol.ProductId = p.Id
    JOIN Branches b ON o.BranchId = b.Id
    WHERE o.DateCreatedUtc >= @StartDate
      AND o.DateCreatedUtc < DATEADD(DAY, 1, @EndDate)
      AND b.IsDeleted = 0
    GROUP BY
        b.Name,
        p.ProductFamily,
        LEFT(p.Sku, 3),
        p.Sku,
        p.Name,
        YEAR(o.DateCreatedUtc),
        DATEPART(WEEK, o.DateCreatedUtc),
        DATEADD(WEEK, DATEDIFF(WEEK, 0, o.DateCreatedUtc), 0)
)
SELECT
    BranchName,
    ProductFamily,
    Channel,
    Sku,
    ProductName,
    CONCAT(YearWeek, ' (', WeekStart, ' ~ ', WeekEnd, ')') AS YearWeekPeriod,
    TotalQty,
    TotalAmount,
    OrderCount,
    CASE WHEN TotalQty > 0 THEN TotalAmount / TotalQty ELSE 0 END AS AvgUnitPrice
FROM WeeklySales
ORDER BY YearWeek, BranchName, ProductFamily, Channel, Sku;
