import unittest

from sales_demand import (
    SalesDemandRecord,
    _pick_applied_daily,
    _qty_for_window,
    scope_demand_index_for_island,
)


class SalesDemandTest(unittest.TestCase):
    def test_applied_column(self):
        row = {"采用需求": 1.5, "需求来源": "8-30天"}
        daily, src = _pick_applied_daily(row, {})
        self.assertAlmostEqual(daily, 1.5)
        self.assertIn("8-30", src)

    def test_window_fallback(self):
        row = {}
        windows = {"8-30": 23.0, "15": 0.0}
        daily, src = _pick_applied_daily(row, windows)
        self.assertAlmostEqual(daily, 1.0)
        self.assertEqual(src, "8-30天")

    def test_qty_column_fuzzy(self):
        row = {"8-30天": 46}
        self.assertAlmostEqual(_qty_for_window(row, "8-30"), 46.0)

    def test_v4_avg_daily_column(self):
        from sales_demand import _pick_applied_daily

        row = {"AvgDailyDemand_3Checkins_Avg": 0.42, "需求来源": "8-30天"}
        daily, src = _pick_applied_daily(row, {})
        self.assertAlmostEqual(daily, 0.42)
        self.assertIn("8-30", src)

    def test_island_scope_fallback_when_no_region_column(self):
        rec = SalesDemandRecord(
            sku="130-001",
            channel="130",
            region="",
            avg_daily_units=2.0,
            demand_source="8-30天",
            qty_windows={},
            unit_volume_m3=None,
            stockout_rate_pct=None,
            raw_name="",
        )
        index = {("130001", "130", ""): rec}
        scoped, note = scope_demand_index_for_island(index, "北岛")
        self.assertEqual(len(scoped), 1)
        self.assertIn("全国", note or "")

    def test_v4_max_windows(self):
        from sales_demand import _pick_applied_daily

        row = {}
        windows = {"8-30": 23.0, "15": 30.0, "30": 60.0}
        daily, src = _pick_applied_daily(row, windows)
        self.assertAlmostEqual(daily, 2.0)
        self.assertIn("30天", src)


if __name__ == "__main__":
    unittest.main()
