from __future__ import annotations

import os
from enum import Enum
from typing import Any, Mapping, Optional


class SourcePolicyConfigurationError(ValueError):
    """Raised when a source-policy environment variable is invalid."""


class PolicyContext(str, Enum):
    INTERNAL = "internal"
    CUSTOMER_API = "customer_api"
    EXPLORER = "explorer"
    EXPORT = "export"
    MATCHER = "matcher"
    PUBLIC_SUMMARY = "public_summary"


KNOWN_PLATFORMS = frozenset(
    {
        "kalshi",
        "polymarket",
        "predictit",
        "manifold",
        "metaculus",
    }
)

PLATFORM_ALIASES = {
    "predict-it": "predictit",
    "predict_it": "predictit",
    "poly-market": "polymarket",
    "meta-culus": "metaculus",
}

DEFAULT_INTERNAL_PLATFORMS = (
    "kalshi,polymarket,predictit"
)

_ENV_BY_CONTEXT = {
    PolicyContext.INTERNAL: "INTERNAL_DATA_PLATFORMS",
    PolicyContext.CUSTOMER_API: "CUSTOMER_API_PLATFORMS",
    PolicyContext.EXPLORER: "EXPLORER_DATA_PLATFORMS",
    PolicyContext.EXPORT: "CUSTOMER_EXPORT_PLATFORMS",
    PolicyContext.MATCHER: "CUSTOMER_MATCHER_PLATFORMS",
    PolicyContext.PUBLIC_SUMMARY: "PUBLIC_SUMMARY_PLATFORMS",
}

_FALLBACK_CONTEXT = {
    PolicyContext.EXPLORER: PolicyContext.CUSTOMER_API,
    PolicyContext.PUBLIC_SUMMARY: PolicyContext.CUSTOMER_API,
}


def normalize_platform(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return PLATFORM_ALIASES.get(normalized, normalized)


def _parse_platform_csv(
    raw_value: Optional[str],
    *,
    variable_name: str,
) -> tuple[str, ...]:
    if raw_value is None:
        return ()

    values: list[str] = []
    unknown: list[str] = []

    for item in str(raw_value).split(","):
        platform = normalize_platform(item)
        if not platform:
            continue
        if platform not in KNOWN_PLATFORMS:
            unknown.append(platform)
            continue
        if platform not in values:
            values.append(platform)

    if unknown:
        raise SourcePolicyConfigurationError(
            f"{variable_name} contains unknown platform(s): "
            + ", ".join(sorted(set(unknown)))
        )

    return tuple(values)


def allowed_platforms(
    context: PolicyContext | str,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[str, ...]:
    policy_context = PolicyContext(context)
    environment: Mapping[str, str] = environ or os.environ
    variable_name = _ENV_BY_CONTEXT[policy_context]

    if variable_name in environment:
        return _parse_platform_csv(
            environment.get(variable_name),
            variable_name=variable_name,
        )

    fallback = _FALLBACK_CONTEXT.get(policy_context)
    if fallback is not None:
        return allowed_platforms(
            fallback,
            environ=environment,
        )

    if policy_context is PolicyContext.INTERNAL:
        return _parse_platform_csv(
            DEFAULT_INTERNAL_PLATFORMS,
            variable_name="DEFAULT_INTERNAL_PLATFORMS",
        )

    return ()


def is_platform_allowed(
    platform: Any,
    context: PolicyContext | str,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    normalized = normalize_platform(platform)
    return bool(
        normalized
        and normalized
        in allowed_platforms(context, environ=environ)
    )


def policy_snapshot(
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, list[str]]:
    return {
        context.value: list(
            allowed_platforms(context, environ=environ)
        )
        for context in PolicyContext
    }


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _sql_string_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _persistent_relation(
    connection: Any,
    table_name: str,
) -> Optional[str]:
    row = connection.execute(
        """
        SELECT
            table_catalog,
            table_schema
        FROM information_schema.tables
        WHERE table_name = ?
          AND table_type IN ('BASE TABLE', 'VIEW')
          AND table_schema <> 'temp'
        ORDER BY
            CASE WHEN table_schema = 'main' THEN 0 ELSE 1 END,
            table_catalog,
            table_schema
        LIMIT 1
        """,
        [table_name],
    ).fetchone()

    if not row:
        return None

    catalog, schema = row
    return ".".join(
        (
            _quote_identifier(str(catalog)),
            _quote_identifier(str(schema)),
            _quote_identifier(table_name),
        )
    )


def install_platform_policy_view(
    connection: Any,
    *,
    table_name: str,
    platform_column: str,
    context: PolicyContext | str,
    required: bool = True,
) -> bool:
    source_relation = _persistent_relation(
        connection,
        table_name,
    )
    if source_relation is None:
        if required:
            raise RuntimeError(
                f"Required policy source table was not found: "
                f"{table_name}"
            )
        return False

    platforms = allowed_platforms(context)
    if platforms:
        allowed_sql = ", ".join(
            _sql_string_literal(item)
            for item in platforms
        )
        predicate = (
            f"LOWER(COALESCE("
            f"{_quote_identifier(platform_column)}, '')) "
            f"IN ({allowed_sql})"
        )
    else:
        predicate = "FALSE"

    connection.execute(
        f"""
        CREATE TEMP VIEW {_quote_identifier(table_name)} AS
        SELECT *
        FROM {source_relation}
        WHERE {predicate}
        """
    )
    return True


def install_market_policy_view(
    connection: Any,
    context: PolicyContext | str = PolicyContext.CUSTOMER_API,
) -> None:
    install_platform_policy_view(
        connection,
        table_name="market_snapshots",
        platform_column="platform",
        context=context,
        required=True,
    )


def install_semantic_policy_views(
    connection: Any,
    context: PolicyContext | str = PolicyContext.MATCHER,
) -> None:
    for table_name in (
        "market_semantics_live",
        "event_contracts",
        "matcher_diagnostics",
    ):
        install_platform_policy_view(
            connection,
            table_name=table_name,
            platform_column="platform",
            context=context,
            required=False,
        )
