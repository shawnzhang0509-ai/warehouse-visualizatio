-- 店面后仓库存（%Storage% 仓）→ Output-NZ/storage.csv
-- 与 display.sql（%Display% 陈列区）分开导出，不要覆盖 display.csv
--
-- 看板用法：
--   display.csv  = 陈列区有没有摆出来
--   storage.csv  = 店面后仓有没有货
--   stock.csv    = 中心仓（Carbine / Walls / GC）有没有货

SELECT
    w.Name AS WarehouseName,
    p.ProductFamily,
    p.Sku,
    p.Name AS ProductName,
    MAX(ISNULL(img.ImageUrl, '')) AS ImageUrl,
    SUM(s.Quantity) AS StorageQty
FROM [dbo].[Stocks] s
INNER JOIN [dbo].[Warehouses] w
    ON s.WarehouseId = w.Id
INNER JOIN [dbo].[Products] p
    ON s.ProductId = p.Id

LEFT JOIN (
    SELECT
        ProductId,
        'https://ierpapi.ifurniture.co.nz/' + REPLACE(RelativeFilePath, '\', '/') AS ImageUrl
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

WHERE w.Name LIKE '%Storage%'
  AND s.StockStatus = 'Normal'
  AND s.StockOnHoldStatus IS NULL

GROUP BY
    w.Name,
    p.ProductFamily,
    p.Sku,
    p.Name

HAVING SUM(s.Quantity) > 0

ORDER BY w.Name, p.ProductFamily, StorageQty DESC;
