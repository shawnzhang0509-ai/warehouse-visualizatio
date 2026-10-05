import unittest
from pathlib import Path
import tempfile

from channel_prefixes import (
    channel_group_key,
    list_merged_channel_filter_options,
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
