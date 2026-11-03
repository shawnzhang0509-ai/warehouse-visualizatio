-- 在产 SKU：库存 + 原价/促销价（按 SKU 一行）→ Output-CA/stock.csv
-- 停产 SKU 见同目录 stock_discontinued.sql → stock_discontinued.csv
--
-- 用法:
--   SSMS: 改 @SkuFilter = '855' 只查某系列；留空 '' 查全部在产
--   本地执行器: 与 stock_discontinued.sql 一起执行导出
--
-- Calgary 库存 = Calgary Warehouse 46 St + Calgary in Transit - Presale-Calgary Hold
-- Edmonton 库存 = Edmonton Warehouse + Edmonton Warehouse-B + Edmonton in Transit - Presale-Edmonton Hold

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
            THEN 'https://ierpapi.ifurniture.co.nz/' + REPLACE(img.RelativeFilePath, '\\', '/')
            ELSE ''
        END
    ) AS ImageUrl,

    -- Calgary Inventory
    SUM(
        CASE
            WHEN TRIM(w.Name) IN (
                'Calgary Warehouse 46 St',
                'Calgary in Transit'
            )
            THEN ISNULL(s.Quantity, 0)
            ELSE 0
        END
    ) - ISNULL(MAX(oh_calgary.TotalOnHold), 0) AS CalgaryStock,

    -- Edmonton Inventory
    SUM(
        CASE
            WHEN TRIM(w.Name) IN (
                'Edmonton Warehouse',
                'Edmonton Warehouse-B',
                'Edmonton in Transit'
            )
            THEN ISNULL(s.Quantity, 0)
            ELSE 0
        END
    ) - ISNULL(MAX(oh_edmonton.TotalOnHold), 0) AS EdmontonStock,

    -- 加拿大全国合计
    SUM(
        CASE
            WHEN TRIM(w.Name) IN (
                'Calgary Warehouse 46 St',
                'Calgary in Transit',
                'Edmonton Warehouse',
                'Edmonton Warehouse-B',
                'Edmonton in Transit'
            )
            THEN ISNULL(s.Quantity, 0)
            ELSE 0
        END
    ) - ISNULL(MAX(oh_calgary.TotalOnHold), 0)
      - ISNULL(MAX(oh_edmonton.TotalOnHold), 0) AS CanadaTotal

FROM [dbo].[Products] p

-- 促销价：取当前生效促销里的最低价
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

-- 产品默认图：优先 IsDefaultProductPicture=1，否则取最新上传图
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

-- Presale-Calgary 的 Hold 数量（需从 Calgary 库存中扣除）
LEFT JOIN (
    SELECT
        s2.ProductId,
        SUM(ISNULL(s2.Quantity, 0)) AS TotalOnHold
    FROM [dbo].[Stocks] s2
    JOIN [dbo].[Warehouses] w2
        ON s2.WarehouseId = w2.Id
    WHERE TRIM(w2.Name) = 'Presale-Calgary'
      AND s2.StockOnHoldStatus IS NOT NULL
    GROUP BY s2.ProductId
) oh_calgary
    ON oh_calgary.ProductId = p.Id

-- Presale-Edmonton 的 Hold 数量（需从 Edmonton 库存中扣除）
LEFT JOIN (
    SELECT
        s3.ProductId,
        SUM(ISNULL(s3.Quantity, 0)) AS TotalOnHold
    FROM [dbo].[Stocks] s3
    JOIN [dbo].[Warehouses] w3
        ON s3.WarehouseId = w3.Id
    WHERE TRIM(w3.Name) = 'Presale-Edmonton'
      AND s3.StockOnHoldStatus IS NOT NULL
    GROUP BY s3.ProductId
) oh_edmonton
    ON oh_edmonton.ProductId = p.Id

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
