import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import panel_data


class ExportFilteredTests(unittest.TestCase):
    def test_filename_slug_keeps_chinese_and_strips_junk(self):
        self.assertEqual(panel_data.export_filename_slug("On Hold - Unpaid Order"), "On_Hold_-_Unpaid_Order")
        self.assertEqual(panel_data.export_filename_slug("  "), "all")
        self.assertIn("未展示", panel_data.export_filename_slug("有货未展示"))

    def test_write_export_csv_only_writes_given_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            rows = [
                {"编码": "581-012", "名称": "A", "数量": 1},
                {"编码": "588-025", "名称": "B", "数量": 2},
            ]
            panel_data.write_export_csv(path, ["编码", "名称", "数量"], rows)
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("编码,名称,数量", text)
            self.assertIn("581-012", text)
            self.assertIn("588-025", text)
            self.assertEqual(text.count("\n"), 3)

    def test_format_onhold_export_row_blank_days(self):
        row = panel_data.format_onhold_export_row({
            "code": "581-012",
            "name": "Chair",
            "status": "On Hold - Unpaid Order",
            "order_no": "2509271033",
            "ticket_no": "DDKDEI",
            "hold_days": None,
            "qty": "1.0",
            "warehouse": "CHCH",
        })
        self.assertEqual(row["hold_days"], "")
        self.assertEqual(row["qty"], 1)
        self.assertEqual(row["code"], "581-012")

    def test_list_on_hold_analysis_respects_status_and_days(self):
        bundle = {
            "on_hold_rows": [
                {
                    "Sku": "111-001",
                    "ProductName": "Keep",
                    "StockOnHoldStatus": "On Hold - Unpaid Order",
                    "OrderNo": "A1",
                    "TicketNo": "T1",
                    "OnHoldDate": "2025-01-01",
                    "Qty": 1,
                    "WarehouseName": "CHCH",
                },
                {
                    "Sku": "111-002",
                    "ProductName": "Skip status",
                    "StockOnHoldStatus": "On Hold - Paid Order",
                    "OrderNo": "A2",
                    "TicketNo": "T2",
                    "OnHoldDate": "2025-01-01",
                    "Qty": 1,
                    "WarehouseName": "CHCH",
                },
                {
                    "Sku": "111-003",
                    "ProductName": "Skip days",
                    "StockOnHoldStatus": "On Hold - Unpaid Order",
                    "OrderNo": "A3",
                    "TicketNo": "T3",
                    "OnHoldDate": "2026-09-01",
                    "Qty": 1,
                    "WarehouseName": "CHCH",
                },
            ]
        }
        rows, total = panel_data.list_on_hold_analysis(
            bundle,
            status_filter="On Hold - Unpaid Order",
            min_hold_days=360,
            max_rows=0,
        )
        codes = [r["code"] for r in rows]
        self.assertEqual(codes, ["111-001"])
        self.assertEqual(total, 1)
        export_rows = [panel_data.format_onhold_export_row(r) for r in rows]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onhold.csv"
            headers = [panel_data.ONHOLD_EXPORT_HEADERS[k] for k in panel_data.ONHOLD_EXPORT_FIELDS]
            named = [{panel_data.ONHOLD_EXPORT_HEADERS[k]: r[k] for k in panel_data.ONHOLD_EXPORT_FIELDS} for r in export_rows]
            panel_data.write_export_csv(path, headers, named)
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("111-001", text)
            self.assertNotIn("111-002", text)
            self.assertNotIn("111-003", text)


if __name__ == "__main__":
    unittest.main()
