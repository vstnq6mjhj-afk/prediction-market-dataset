from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from api.routes.billing_v2 import visible_terms


class MonthlyLaunchTests(unittest.TestCase):
    def test_monthly_is_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(visible_terms(), ("monthly",))

    def test_empty_value_fails_safe_to_monthly(self):
        with patch.dict(
            os.environ,
            {"BILLING_VISIBLE_TERMS": ""},
            clear=True,
        ):
            self.assertEqual(visible_terms(), ("monthly",))

    def test_monthly_can_be_explicit(self):
        with patch.dict(
            os.environ,
            {"BILLING_VISIBLE_TERMS": "monthly"},
            clear=True,
        ):
            self.assertEqual(visible_terms(), ("monthly",))

    def test_longer_terms_can_be_enabled_later(self):
        with patch.dict(
            os.environ,
            {
                "BILLING_VISIBLE_TERMS":
                    "monthly,3-month,6-month,annual"
            },
            clear=True,
        ):
            self.assertEqual(
                visible_terms(),
                ("monthly", "3-month", "6-month", "annual"),
            )

    def test_unknown_term_is_rejected(self):
        with patch.dict(
            os.environ,
            {"BILLING_VISIBLE_TERMS": "monthly,weekly"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                visible_terms()


if __name__ == "__main__":
    unittest.main()
