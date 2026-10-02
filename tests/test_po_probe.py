from po_probe import build_po_row_count_batch, parse_sku_filter, warn_po_sql_patterns


def test_parse_sku_filter():
    sql = "DECLARE @SkuFilter VARCHAR(20) = '996';\nSELECT 1"
    assert parse_sku_filter(sql) == "996"


def test_warn_like_sku_filter_without_percent():
    sql = "WHERE p.Sku LIKE @SkuFilter AND pol.QuantityOrdered > 0"
    assert warn_po_sql_patterns(sql)


def test_build_count_batch_keeps_declare():
    sql = """DECLARE @SkuFilter VARCHAR(20) = '';
SELECT p.Sku FROM dbo.Products p
WHERE (@SkuFilter = '' OR p.Sku LIKE @SkuFilter + '%')
ORDER BY p.Sku"""
    batch = build_po_row_count_batch(sql)
    assert batch is not None
    assert "DECLARE @SkuFilter" in batch
    assert "COUNT_BIG" in batch
    assert "ORDER BY" not in batch.split("FROM (")[1]
