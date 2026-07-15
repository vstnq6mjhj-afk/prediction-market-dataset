from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from api.source_policy import (
    PolicyContext,
    SourcePolicyConfigurationError,
    allowed_platforms,
    install_platform_policy_view,
    is_platform_allowed,
)


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConnection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "information_schema.tables" in sql:
            return _Cursor(("warehouse", "main"))
        return _Cursor()


class SourcePolicyTests(unittest.TestCase):
    def test_customer_contexts_deny_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                allowed_platforms(PolicyContext.CUSTOMER_API),
                (),
            )
            self.assertEqual(
                allowed_platforms(PolicyContext.EXPORT),
                (),
            )
            self.assertEqual(
                allowed_platforms(PolicyContext.MATCHER),
                (),
            )

    def test_internal_context_has_safe_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                allowed_platforms(PolicyContext.INTERNAL),
                (
                    "kalshi",
                    "polymarket",
                    "predictit",
                    "manifold",
                ),
            )

    def test_explorer_inherits_customer_api(self):
        with patch.dict(
            os.environ,
            {"CUSTOMER_API_PLATFORMS": "kalshi,polymarket"},
            clear=True,
        ):
            self.assertEqual(
                allowed_platforms(PolicyContext.EXPLORER),
                ("kalshi", "polymarket"),
            )

    def test_explicit_empty_explorer_is_empty(self):
        with patch.dict(
            os.environ,
            {
                "CUSTOMER_API_PLATFORMS": "kalshi",
                "EXPLORER_DATA_PLATFORMS": "",
            },
            clear=True,
        ):
            self.assertEqual(
                allowed_platforms(PolicyContext.EXPLORER),
                (),
            )

    def test_unknown_platform_fails(self):
        with patch.dict(
            os.environ,
            {"CUSTOMER_API_PLATFORMS": "kalshi,unknown"},
            clear=True,
        ):
            with self.assertRaises(SourcePolicyConfigurationError):
                allowed_platforms(PolicyContext.CUSTOMER_API)

    def test_alias_normalization(self):
        with patch.dict(
            os.environ,
            {"CUSTOMER_API_PLATFORMS": "predict-it"},
            clear=True,
        ):
            self.assertTrue(
                is_platform_allowed(
                    "predictit",
                    PolicyContext.CUSTOMER_API,
                )
            )

    def test_policy_view_contains_only_allowlist(self):
        connection = _FakeConnection()
        with patch.dict(
            os.environ,
            {"CUSTOMER_API_PLATFORMS": "kalshi,polymarket"},
            clear=True,
        ):
            install_platform_policy_view(
                connection,
                table_name="market_snapshots",
                platform_column="platform",
                context=PolicyContext.CUSTOMER_API,
            )

        create_sql = connection.statements[-1][0].lower()
        self.assertIn("create temp view", create_sql)
        self.assertIn("'kalshi'", create_sql)
        self.assertIn("'polymarket'", create_sql)
        self.assertNotIn("'manifold'", create_sql)

    def test_empty_allowlist_creates_false_view(self):
        connection = _FakeConnection()
        with patch.dict(os.environ, {}, clear=True):
            install_platform_policy_view(
                connection,
                table_name="market_snapshots",
                platform_column="platform",
                context=PolicyContext.CUSTOMER_API,
            )

        create_sql = connection.statements[-1][0].upper()
        self.assertIn("WHERE FALSE", create_sql)


if __name__ == "__main__":
    unittest.main()
