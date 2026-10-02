from sql_placeholders import apply_sql_placeholders, placeholders_in_sql


def test_like_sku_percent_with_prefix():
    sql = "WHERE p.Sku LIKE '{sku}%'"
    out, notes = apply_sql_placeholders(sql, {"sku": "130"})
    assert "LIKE '130%'" in out
    assert "{sku}" not in out
    assert notes


def test_like_sku_percent_empty_means_all():
    sql = "WHERE p.Sku LIKE '{sku}%'"
    out, _ = apply_sql_placeholders(sql, {"sku": ""})
    assert "LIKE '%'" in out


def test_unsubstituted_detected():
    assert placeholders_in_sql("LIKE '{sku}%'")


def test_channel_list_replaces_like():
    sql = "WHERE p.Sku LIKE '{sku}%'"
    prefixes = ["130", "378"]
    out, notes = apply_sql_placeholders(sql, {"sku": "", "channel_prefixes": prefixes})
    assert "LEFT(p.Sku, 3) IN ('130', '378')" in out
    assert "渠道列表" in notes[0]
