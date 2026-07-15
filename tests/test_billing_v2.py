from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from api.routes.billing_v2 import (
    PLAN_CONFIG,
    TERMS,
    amount_pence,
    checkout_readiness,
    effective_monthly_pence,
    infer_billing_term,
    infer_plan,
    saving_percent,
)


class BillingV2Tests(unittest.TestCase):
    def test_supported_terms(self):
        self.assertEqual(
            list(TERMS),
            [
                "monthly",
                "3-month",
                "6-month",
                "annual",
            ],
        )

    def test_stripe_intervals(self):
        self.assertEqual(
            (
                TERMS["3-month"].interval,
                TERMS["3-month"].interval_count,
            ),
            ("month", 3),
        )
        self.assertEqual(
            (
                TERMS["6-month"].interval,
                TERMS["6-month"].interval_count,
            ),
            ("month", 6),
        )
        self.assertEqual(
            (
                TERMS["annual"].interval,
                TERMS["annual"].interval_count,
            ),
            ("year", 1),
        )

    def test_default_amounts_have_no_discount(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                amount_pence("developer", "3-month"),
                5700,
            )
            self.assertEqual(
                amount_pence("professional", "annual"),
                58800,
            )
            self.assertEqual(
                saving_percent("developer", "annual"),
                0,
            )

    def test_amount_override_and_saving(self):
        with patch.dict(
            os.environ,
            {
                "STRIPE_DEVELOPER_ANNUAL_AMOUNT_PENCE":
                    "18200",
            },
            clear=True,
        ):
            self.assertEqual(
                amount_pence("developer", "annual"),
                18200,
            )
            self.assertEqual(
                effective_monthly_pence(
                    "developer",
                    "annual",
                ),
                1517,
            )
            self.assertEqual(
                saving_percent("developer", "annual"),
                20,
            )

    def test_term_from_metadata(self):
        subscription = {
            "metadata": {
                "billing_term": "6-month",
            }
        }
        self.assertEqual(
            infer_billing_term(subscription),
            "6-month",
        )

    def test_term_from_recurring_price(self):
        subscription = {
            "items": {
                "data": [
                    {
                        "price": {
                            "recurring": {
                                "interval": "month",
                                "interval_count": 3,
                            }
                        }
                    }
                ]
            }
        }
        self.assertEqual(
            infer_billing_term(subscription),
            "3-month",
        )

    def test_plan_from_metadata(self):
        self.assertEqual(
            infer_plan(
                {
                    "metadata": {
                        "plan": "professional",
                    }
                }
            ),
            "professional",
        )

    def test_checkout_disabled_by_default(self):
        with patch.dict(
            os.environ,
            {
                "BILLING_CHECKOUT_ENABLED": "false",
            },
            clear=False,
        ):
            ready, _ = checkout_readiness()
            self.assertFalse(ready)


if __name__ == "__main__":
    unittest.main()
