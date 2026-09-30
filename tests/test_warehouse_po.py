import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import warehouse_volume as wv


class WarehousePoTest(unittest.TestCase):
    def test_parse_po_and_merge_channels(self):
        rows = [
            {"Sku": "271-001", "Quantity": 10, "VolumeM3": 69.0},
            {"Sku": "155-002", "Quantity": 5, "VolumeWithBox": 13.8},
        ]
        lines = wv._parse_po_rows(rows)
        self.assertEqual(len(lines), 2)
        self.assertAlmostEqual(lines[0]["volume_containers"], 1.0, places=2)
        self.assertEqual(lines[0]["channel"], "271")

        with mock.patch.object(wv, "load_po_lines", return_value=(lines, None, Path("po.csv"))):
            po_report = wv.build_po_report("NZ")
        self.assertAlmostEqual(po_report["total_po_containers"], 2.0, places=2)

        merged = wv.merge_channel_breakdown(
            [{"channel": "271", "volume_containers": 3.0}],
            po_report,
        )
        self.assertEqual(merged[0]["channel"], "271")
        self.assertEqual(merged[0]["po_containers"], 1.0)
        self.assertEqual(merged[0]["total_containers"], 4.0)

    def test_stock_volume_csv_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            path = out / "stock_volume.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["WarehouseName", "Sku", "VolumeM3"])
                w.writerow(["Walls Road", "271-001", "138"])
            with mock.patch.object(wv, "_region_output_dir", return_value=out):
                by_wh, by_ch, found = wv._load_stock_volume_maps("NZ")
            self.assertEqual(found, path)
            self.assertAlmostEqual(by_wh["Walls Road"], 2.0, places=2)
            self.assertAlmostEqual(by_ch["271"], 2.0, places=2)


if __name__ == "__main__":
    unittest.main()
