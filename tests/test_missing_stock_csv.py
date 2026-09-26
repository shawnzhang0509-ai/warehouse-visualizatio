import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import panel_data


class MissingStockCsvTests(unittest.TestCase):
    def test_message_mentions_copy_or_export(self):
        msg = panel_data._missing_output_csv_message(
            ROOT / "Output-NZ" / "stock.csv",
            "stock",
            "NZ",
            ROOT / "Output-NZ",
        )
        self.assertIn("Output-NZ/stock.csv", msg)
        self.assertIn("拷贝", msg)
        self.assertIn("app.py", msg)

    def test_load_region_bundle_raises_helpful_error(self):
        panel_data.clear_region_cache("NZ")
        with self.assertRaises(FileNotFoundError) as ctx:
            panel_data._load_region_bundle("NZ", force=True)
        text = str(ctx.exception)
        self.assertIn("stock", text)
        self.assertIn("Output-NZ", text)


if __name__ == "__main__":
    unittest.main()
