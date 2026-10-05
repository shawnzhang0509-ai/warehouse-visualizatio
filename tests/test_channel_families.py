import unittest
from pathlib import Path
import tempfile

from channel_prefixes import (
    channel_group_key,
    family_for_channel_link,
    list_merged_channel_filter_options,
    load_channel_families_from_po_prefixes,
    load_channel_families_from_txt,
    parse_named_channel_folder,
    resolve_channel_filter,
    scan_channel_families_from_dirs,
    sub_to_family_map,
)


class ChannelFamiliesTest(unittest.TestCase):
    def test_parse_named_folder(self):
        self.assertEqual(parse_named_channel_folder("河北_321"), ("河北", "321"))
        self.assertEqual(parse_named_channel_folder("山东_446"), ("山东", "446"))
        self.assertEqual(parse_named_channel_folder("996"), ("", "996"))

    def test_scan_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "河北_321").mkdir()
            (base / "河北_352").mkdir()
            (base / "996").mkdir()
            fam = scan_channel_families_from_dirs(base)
            self.assertEqual(set(fam["河北"]), {"321", "352"})
            self.assertNotIn("996", fam)

    def test_families_txt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "channel_families.txt"
            path.write_text("# c\n河北_321\n山东_446\n", encoding="utf-8")
            fam = load_channel_families_from_txt(path)
            self.assertEqual(fam["河北"], ["321"])
            self.assertEqual(fam["山东"], ["446"])

    def test_po_prefixes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "po_channel_prefixes.txt"
            path.write_text("河北_321\n河北_352\n山东_446\n378\n", encoding="utf-8")
            fam = load_channel_families_from_po_prefixes(path)
            self.assertEqual(set(fam["河北"]), {"321", "352"})
            self.assertEqual(fam["山东"], ["446"])
            self.assertNotIn("378", fam)

    def test_family_for_link(self):
        families = {"河北": ["321", "352"]}
        self.assertEqual(family_for_channel_link("河北", families), "河北")
        self.assertEqual(family_for_channel_link("321", families), "河北")

    def test_group_and_filter(self):
        families = {"河北": ["321", "352"], "山东": ["446"]}
        sub_map = sub_to_family_map(families)
        self.assertEqual(channel_group_key("321", sub_map), "河北")
        self.assertEqual(channel_group_key("130", sub_map), "130")
        mode, allowed = resolve_channel_filter("河北", families)
        self.assertEqual(mode, "family")
        self.assertEqual(allowed, {"321", "352"})
        opts = list_merged_channel_filter_options(["321", "352", "130"], families)
        self.assertIn("河北", opts)
        self.assertIn("山东", opts)
        self.assertIn("130", opts)
        self.assertNotIn("321", opts)


if __name__ == "__main__":
    unittest.main()
