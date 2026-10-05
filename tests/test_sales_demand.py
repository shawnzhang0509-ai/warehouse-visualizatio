import unittest

from sales_demand import _pick_applied_daily, _qty_for_window


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

    def test_v4_max_windows(self):
        from sales_demand import _pick_applied_daily

        row = {}
        windows = {"8-30": 23.0, "15": 30.0, "30": 60.0}
        daily, src = _pick_applied_daily(row, windows)
        self.assertAlmostEqual(daily, 2.0)
        self.assertIn("30天", src)


if __name__ == "__main__":
    unittest.main()
