import unittest

import volume_tab_ui as vol_ui
import warehouse_volume as wv


class VolumeTabUiTests(unittest.TestCase):
    def test_volume_kpis_from_report(self):
        report = {
            "data": [
                {"row_type": "warehouse", "name": "Walls", "volume_containers": 100, "utilization_pct": 92.0},
                {"row_type": "warehouse", "name": "Carbine", "volume_containers": 50, "utilization_pct": 70.0},
            ],
            "po": {"total_po_containers": 80, "island_totals": {"北岛": 60, "南岛": 20}},
        }
        k = vol_ui.volume_kpis_from_report(report)
        self.assertEqual(k["stock_containers"], 150.0)
        self.assertEqual(k["po_containers"], 80.0)
        self.assertEqual(k["total_containers"], 230.0)
        self.assertEqual(len(k["high_util"]), 1)
        self.assertEqual(k["north_po_pct"], 75.0)

    def test_chch_display_warehouses_excluded_from_volume(self):
        master = {}
        for name in (
            "CHCH Display",
            "CHCH Display Colombo",
            "CHCH Shop Storage",
            "CHCH Treffers",
            "CHCH Colombo Shop Storage",
        ):
            self.assertTrue(wv._volume_stock_warehouse_excluded(name, master))


if __name__ == "__main__":
    unittest.main()
