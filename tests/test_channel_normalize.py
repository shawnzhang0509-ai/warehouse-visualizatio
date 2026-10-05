import unittest

from channel_prefixes import normalize_sku_channel_code
from sales_demand import load_sales_demand_index
import panel_data as pd


class ChannelNormalizeTest(unittest.TestCase):
    def test_float_channel(self):
        self.assertEqual(normalize_sku_channel_code("996.0"), "996")
        self.assertEqual(normalize_sku_channel_code(996), "996")

    def test_merge_sales_channel_float(self):
        from sales_demand import _merge_row, _pick_applied_daily

        acc = {}
        row = {
            "Sku": "996-1001",
            "Channel": 996.0,
            "Region": "北岛",
            "AvgDailyDemand_3Checkins_Avg": 1.2,
        }
        _merge_row(acc, row, "8-30", 23)
        self.assertEqual(len(acc), 1)
        key = next(iter(acc))
        self.assertEqual(key[0], pd.sku_join_key("996-1001"))
        self.assertEqual(key[1], "996")
        slot = acc[key]
        avg, _src = _pick_applied_daily(slot["row"], slot["windows"])
        self.assertAlmostEqual(avg, 1.2)


if __name__ == "__main__":
    unittest.main()
