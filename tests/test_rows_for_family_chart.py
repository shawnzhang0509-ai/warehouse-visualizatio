import unittest

import inventory_health as ih


class RowsForFamilyChartTest(unittest.TestCase):
    def test_main_view_merges_provinces_and_other(self):
        families = {"河北": ["321", "378"], "山东": ["446"]}
        detail = [
            {
                "sku": "321-001",
                "sub_channel": "321",
                "avg_daily_units": 10,
                "avg_daily_demand_m3": 1.0,
                "inventory_volume_m3": 10.0,
                "inventory_units": 5,
                "stockout_rate_pct": 20.0,
                "theoretical_days": 10.0,
                "theoretical_days_label": "10",
                "transit_volume_m3": 0,
                "quadrant": "Healthy",
                "priority": 3,
                "bubble_m3_day": 1.0,
            },
            {
                "sku": "378-001",
                "sub_channel": "378",
                "avg_daily_units": 5,
                "avg_daily_demand_m3": 0.5,
                "inventory_volume_m3": 5.0,
                "inventory_units": 2,
                "stockout_rate_pct": 30.0,
                "theoretical_days": 10.0,
                "theoretical_days_label": "10",
                "transit_volume_m3": 0,
                "quadrant": "Healthy",
                "priority": 3,
                "bubble_m3_day": 0.5,
            },
            {
                "sku": "446-001",
                "sub_channel": "446",
                "avg_daily_units": 8,
                "avg_daily_demand_m3": 0.8,
                "inventory_volume_m3": 8.0,
                "inventory_units": 4,
                "stockout_rate_pct": 40.0,
                "theoretical_days": 10.0,
                "theoretical_days_label": "10",
                "transit_volume_m3": 0,
                "quadrant": "Healthy",
                "priority": 3,
                "bubble_m3_day": 0.8,
            },
            {
                "sku": "130-001",
                "sub_channel": "130",
                "avg_daily_units": 3,
                "avg_daily_demand_m3": 0.3,
                "inventory_volume_m3": 3.0,
                "inventory_units": 1,
                "stockout_rate_pct": 10.0,
                "theoretical_days": 10.0,
                "theoretical_days_label": "10",
                "transit_volume_m3": 0,
                "quadrant": "Healthy",
                "priority": 3,
                "bubble_m3_day": 0.3,
            },
        ]
        report = {
            "channel_families": families,
            "rows_detail": detail,
            "rows": [],
            "thresholds": {"stockout_pct": 50, "consumption_days": 60, "cover_days_proxy": 14},
        }
        rows = ih.rows_for_family_chart(report)
        labels = {r["channel"] for r in rows}
        self.assertEqual(labels, {"河北", "山东", "其他"})
        hebei = next(r for r in rows if r["channel"] == "河北")
        self.assertAlmostEqual(hebei["avg_daily_demand_m3"], 1.5)


if __name__ == "__main__":
    unittest.main()
