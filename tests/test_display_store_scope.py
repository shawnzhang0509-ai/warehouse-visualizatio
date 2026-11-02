import unittest

import panel_data


DISPLAY_DETAILS = {
    "Onehunga Shop Display": {},
    "Hamilton Shop Display": {
        "970-016": {
            "code": "970-016",
            "family": "Lorina",
            "name": "Lorina U-Shaped Sectional Sofa with Right-Facing Chaise",
        },
    },
    "Westgate display": {},
    "CHCH Display": {
        "870-015": {
            "code": "870-015",
            "family": "Lorina",
            "name": "Lorina U-Shaped Sectional Sofa with Left-Facing Chaise",
        },
    },
}

BY_STORE = {
    "Onehunga Shop Display": {"111-001"},
    "Hamilton Shop Display": {"970-016"},
    "Westgate display": {"222-001"},
    "CHCH Display": {"870-015"},
    "Onehunga Shop-Display": {"333-001"},
}


def _lorina_gap(code="970-015"):
    return {
        "code": code,
        "norm_code": code,
        "family": "Lorina",
        "name": "Lorina U-Shaped Sectional Sofa with Left-Facing Chaise",
        "in_stock": True,
        "displayed": False,
        "gap": True,
        "discontinued": False,
    }


class DisplayStoreScopeTest(unittest.TestCase):
    def test_onehunga_does_not_inherit_hamilton_or_westgate_display(self):
        codes = panel_data._displayed_codes_for_store(
            "Onehunga Shop Display", BY_STORE, "NZ",
        )
        self.assertEqual(codes, {"111-001", "333-001"})
        self.assertNotIn("970-016", codes)
        self.assertNotIn("222-001", codes)

    def test_hamilton_display_stays_on_hamilton(self):
        codes = panel_data._displayed_codes_for_store(
            "Hamilton Shop Display", BY_STORE, "NZ",
        )
        self.assertEqual(codes, {"970-016"})

    def test_onehunga_exemption_ignores_hamilton_anchor(self):
        details = panel_data._display_details_for_store(
            DISPLAY_DETAILS, "Onehunga Shop Display", "NZ",
        )
        self.assertNotIn("970-016", details)
        self.assertNotIn("870-015", details)
        product = _lorina_gap()
        panel_data._apply_family_exemptions([product], details)
        self.assertFalse(product["exempted"])
        self.assertTrue(product["gap"])

    def test_hamilton_exemption_uses_its_own_lorina(self):
        details = panel_data._display_details_for_store(
            DISPLAY_DETAILS, "Hamilton Shop Display", "NZ",
        )
        self.assertIn("970-016", details)
        product = _lorina_gap()
        panel_data._apply_family_exemptions([product], details)
        self.assertTrue(product["exempted"])
        self.assertFalse(product["gap"])
        self.assertIn("970-016", product["exemption_reason"])


if __name__ == "__main__":
    unittest.main()
