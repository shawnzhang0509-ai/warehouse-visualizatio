import unittest
from unittest import mock

import inventory_health as ih


class InventoryHealthCalcTest(unittest.TestCase):
    def test_theoretical_days(self):
        days, label = ih._theoretical_days(1000.0, 10.0)
        self.assertAlmostEqual(days, 100.0)
        self.assertIn("100", label)

    def test_theoretical_days_zero_demand(self):
        days, label = ih._theoretical_days(100.0, 0.0)
        self.assertIsNone(days)
        self.assertEqual(label, "N/A")

    def test_quadrant_mismatch(self):
        th = ih.HealthThresholds(stockout_pct=50, consumption_days=60)
        self.assertEqual(ih._quadrant(80, 90, th), "Inventory Mismatch")

    def test_quadrant_supply_shortage(self):
        th = ih.HealthThresholds(stockout_pct=50, consumption_days=60)
        self.assertEqual(ih._quadrant(80, 10, th), "Supply Shortage")

    def test_island_north_stock_only(self):
        row = {
            "Sku": "130-001",
            "CarbineStock": 10,
            "GeraldConnellyStock": 50,
        }
        self.assertEqual(ih._inventory_units_for_scope(row, "NZ", "北岛"), 10.0)
        self.assertEqual(ih._inventory_units_for_scope(row, "NZ", "南岛"), 50.0)
        self.assertEqual(ih._inventory_units_for_scope(row, "NZ", ""), 60.0)

    def test_build_with_mock_bundle(self):
        stock_rows = [
            {
                "Sku": "130-001",
                "ProductName": "Test Chair",
                "ProductFamily": "Chairs",
                "PriceRadarVolume": 0.5,
                "CarbineStock": 100,
                "IsDiscontinued": 0,
            },
        ]
        weekly = [
            {
                "Sku": "130-001",
                "Channel": "130",
                "TotalQty": 140,
                "WeekStart": "2026-01-01",
            },
        ]
        bundle = {
            "data_dir": "/tmp",
            "stock_raw_rows": stock_rows,
        }
        with mock.patch.object(ih, "_load_stock_rows_region", return_value=(stock_rows, None)):
            with mock.patch.object(ih.pd, "resolve_sources", return_value=(None, None, "mock", "/tmp")):
                with mock.patch.object(ih, "load_sales_demand_index", return_value=({}, [])):
                    with mock.patch.object(ih, "_load_weekly_sales", return_value=(weekly, mock.Mock(), None)):
                        with mock.patch.object(
                            ih, "_load_po_by_sku", return_value=({"130001": 69.0}, None),
                        ):
                            report = ih.build_inventory_health_report(
                                "NZ", thresholds=ih.HealthThresholds(cover_days_proxy=14)
                            )
        rows = report["rows"]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertAlmostEqual(r["avg_daily_units"], 140 / 90, places=2)
        self.assertAlmostEqual(r["avg_daily_demand_m3"], r["avg_daily_units"] * 0.5, places=3)
        self.assertAlmostEqual(r["inventory_volume_m3"], 50.0)
        self.assertAlmostEqual(r["theoretical_days"], 50.0 / r["avg_daily_demand_m3"], places=1)


if __name__ == "__main__":
    unittest.main()
