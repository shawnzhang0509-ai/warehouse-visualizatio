-- 在产 SKU：库存 + 原价/促销价（按 SKU 一行）→ Output-NZ/stock.csv
-- 停产 SKU 见同目录 stock_discontinued.sql → stock_discontinued.csv
--
-- 用法:
--   SSMS: 改 @SkuFilter = '855' 只查某系列；留空 '' 查全部在产
--   本地执行器: Data-NZ 目录下运行 SQL 导出
--
-- 北岛库存 = Carbine Rd Warehouse + Walls / Walls Road / Walls in Transit
-- 南岛库存 = CHCH Gerald Connelly / GC

DECLARE @SkuFilter VARCHAR(20) = '';

SELECT
    p.Sku,
    p.Name AS ProductName,
    ISNULL(p.ProductFamily, '') AS ProductFamily,
    p.PriceRadarVolume,
    CAST(p.IsDiscontinued AS INT) AS IsDiscontinued,
    p.UnitPrice,
    CASE
        WHEN promo.SalePrice IS NOT NULL
         AND promo.SalePrice > 0
         AND promo.SalePrice < p.UnitPrice
        THEN promo.SalePrice
        ELSE p.UnitPrice
    END AS SalePrice,
    CASE
        WHEN promo.SalePrice IS NOT NULL
         AND promo.SalePrice > 0
         AND promo.SalePrice < p.UnitPrice
        THEN 1
        ELSE 0
    END AS OnPromotion,
    MAX(
        CASE
            WHEN img.RelativeFilePath IS NOT NULL
            THEN 'https://ierpapi.ifurniture.co.nz/' + REPLACE(img.RelativeFilePath, '\', '/')
            ELSE ''
        END
    ) AS ImageUrl,
    SUM(CASE WHEN TRIM(w.Name) = 'Carbine Rd Warehouse' THEN ISNULL(s.Quantity, 0) ELSE 0 END) AS CarbineStock,
    SUM(CASE WHEN TRIM(w.Name) IN ('Walls', 'Walls Road', 'Walls in Transit') THEN ISNULL(s.Quantity, 0) ELSE 0 END) AS WallsStock,
    SUM(CASE WHEN TRIM(w.Name) = 'Carbine Rd Warehouse' THEN ISNULL(s.Quantity, 0) ELSE 0 END)
    + SUM(CASE WHEN TRIM(w.Name) IN ('Walls', 'Walls Road', 'Walls in Transit') THEN ISNULL(s.Quantity, 0) ELSE 0 END) AS NorthIslandTotal,
    SUM(CASE WHEN TRIM(w.Name) IN ('CHCH Gerald Connelly', 'GC') THEN ISNULL(s.Quantity, 0) ELSE 0 END) AS GeraldConnellyStock
FROM [dbo].[Products] p

LEFT JOIN (
    SELECT ProductId, SalePrice, PromotionId
    FROM (
        SELECT
            pp.ProductId,
            pp.SalePrice,
            pp.PromotionId,
            ROW_NUMBER() OVER (
                PARTITION BY pp.ProductId
                ORDER BY pp.SalePrice ASC, pp.PromotionId ASC
            ) AS rn
        FROM dbo.ProductPromotions pp
        INNER JOIN dbo.Promotions pr
            ON pp.PromotionId = pr.Id
        WHERE pp.IsDisabled = 0
          AND pr.IsEnabled = 1
          AND GETUTCDATE() BETWEEN pr.StartTimeUtc AND pr.EndTimeUtc
          AND pp.SalePrice IS NOT NULL
          AND pp.SalePrice > 0
    ) t
    WHERE rn = 1
) promo
    ON promo.ProductId = p.Id

LEFT JOIN (
    SELECT ProductId, RelativeFilePath
    FROM (
        SELECT
            PD.ProductId,
            D.RelativeFilePath,
            ROW_NUMBER() OVER (
                PARTITION BY PD.ProductId
                ORDER BY
                    CASE WHEN PD.IsDefaultProductPicture = 1 THEN 0 ELSE 1 END,
                    D.DateUploadedOnUtc DESC
            ) AS rn
        FROM dbo.ProductDocuments PD
        INNER JOIN dbo.Documents D
            ON PD.DocumentId = D.Id
        WHERE NULLIF(LTRIM(RTRIM(D.RelativeFilePath)), '') IS NOT NULL
    ) t
    WHERE rn = 1
) img
    ON img.ProductId = p.Id

LEFT JOIN [dbo].[Stocks] s
    ON s.ProductId = p.Id
    AND s.StockStatus = 'Normal'
    AND s.StockOnHoldStatus IS NULL

LEFT JOIN [dbo].[Warehouses] w
    ON s.WarehouseId = w.Id

WHERE (@SkuFilter = '' OR p.Sku LIKE @SkuFilter + '%')
  AND p.IsDiscontinued = 0

GROUP BY
    p.Sku,
    p.Name,
    p.ProductFamily,
    p.PriceRadarVolume,
    p.IsDiscontinued,
    p.UnitPrice,
    promo.SalePrice

ORDER BY p.Sku;
