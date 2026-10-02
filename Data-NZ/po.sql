-- 在途 PO → Output-NZ/po.csv（仓库内置，优先于本机 PO.txt）
-- 本机 PO.txt 若与 po.sql 同输出 po.csv，执行器会优先用本文件（.sql > .txt）
-- 只查 996 系列：取消下面 WHERE 一行注释，并注释掉「查全部」那行

SELECT
    po.Id AS PurchaseOrderId,
    po.PurchaseOrderCode,
    p.Sku,
    LEFT(p.Sku, 3) AS Channel,
    pol.QuantityOrdered,
    ISNULL(p.VolumeWithBox, 0) AS VolumeWithBox,
    pol.QuantityOrdered * ISNULL(p.VolumeWithBox, 0) AS VolumeM3,
    po.[ETD],
    po.ShippedToPortId,
    c.ActualArrivingDate AS CheckinDate,
    c.ContainerNumber,
    CASE
        WHEN c.ShippedtoportId = '3b9ff26b-4ec0-44c6-92ac-ee9804272426' THEN N'南岛'
        WHEN c.ShippedtoportId = '646a2216-87c2-4bc6-8469-fdbd90bb2d84' THEN N'北岛'
        WHEN po.ShippedToPortId = '3b9ff26b-4ec0-44c6-92ac-ee9804272426' THEN N'南岛'
        WHEN po.ShippedToPortId = '646a2216-87c2-4bc6-8469-fdbd90bb2d84' THEN N'北岛'
        ELSE N'北岛'
    END AS Region
FROM dbo.PurchaseOrders po
INNER JOIN dbo.PurchaseOrderLines pol ON pol.PurchaseOrderId = po.Id
INNER JOIN dbo.Products p ON pol.ProductId = p.Id
LEFT JOIN dbo.Containers c ON c.PurchaseOrderId = po.Id
WHERE pol.QuantityOrdered > 0
  -- AND p.Sku LIKE '996%'
ORDER BY po.POPlacedOnUtc DESC, p.Sku;
