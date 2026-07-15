from __future__ import annotations

import ast
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MAIN_PATH = ROOT / "api" / "main.py"
EXPLORER_PATH = ROOT / "api" / "routes" / "explorer.py"
MARKER = "PHASE15B_SOURCE_POLICY"


class PatchError(RuntimeError):
    pass


def _backup(path: Path) -> None:
    backup = path.with_suffix(path.suffix + ".phase15b.bak")
    if not backup.exists():
        shutil.copy2(path, backup)


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _replace_node(text: str, node: ast.AST, replacement: str) -> str:
    offsets = _line_offsets(text)
    start = offsets[node.lineno - 1]
    end = offsets[node.end_lineno]
    return text[:start] + replacement.rstrip() + "\n" + text[end:]


def _function(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    raise PatchError(f"Function not found: {name}")


def _route_path(node) -> str | None:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not decorator.args:
            continue
        first = decorator.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _insert_import(text: str, *, after: str, block: str) -> str:
    if block.strip() in text:
        return text
    position = text.find(after)
    if position < 0:
        raise PatchError(f"Import anchor not found: {after}")
    position += len(after)
    return text[:position] + "\n" + block.rstrip() + text[position:]


def _patch_main(text: str) -> str:
    if MARKER in text:
        return text

    import_block = f'''
# {MARKER}
from api.source_policy import (
    PolicyContext,
    allowed_platforms,
    install_market_policy_view,
)
'''
    text = _insert_import(
        text,
        after="from api.routes.explorer import router as explorer_router",
        block=import_block,
    )

    tree = ast.parse(text)
    node = _function(tree, "query_db")
    source = ast.get_source_segment(text, node)
    if source is None:
        raise PatchError("Could not read query_db.")

    connect_line = "    conn = duckdb.connect(DB_PATH, read_only=True)\n"
    if connect_line not in source:
        raise PatchError("Expected query_db connection line not found.")

    source = source.replace(
        connect_line,
        connect_line
        + "    install_market_policy_view(\n"
        + "        conn,\n"
        + "        PolicyContext.CUSTOMER_API,\n"
        + "    )\n",
        1,
    )
    text = _replace_node(text, node, source)

    replacements = {
        (
            "Cross-platform prediction market data API covering "
            "Polymarket, Kalshi, "
        ): (
            "Cross-platform prediction market data API with "
            "commercial source availability "
        ),
        (
            '"Manifold, and PredictIt. Includes market search, latest snapshots, "'
        ): (
            '"controlled by the source-policy allowlist. Includes market search, latest snapshots, "'
        ),
        (
            "Unified historical and live prediction market data from Polymarket, Kalshi,\n"
            "        Manifold, and PredictIt — delivered through a REST API, customer dashboard,\n"
        ): (
            "Unified historical and live prediction market data from commercially enabled sources —\n"
            "        delivered through a REST API, customer dashboard,\n"
        ),
        (
            "Supported platforms: Polymarket, Kalshi, Manifold, and PredictIt."
        ): (
            "Commercial source availability varies by licensing status and plan."
        ),
        (
            "The current warehouse covers Polymarket, Kalshi, Manifold, and PredictIt.\n"
            "            Coverage will expand over time."
        ): (
            "The internal warehouse may contain additional sources. Customer availability is\n"
            "            controlled separately by licensing status and plan."
        ),
        '"platforms": "4",': (
            '"platforms": str(len(allowed_platforms('
            'PolicyContext.PUBLIC_SUMMARY))),'
        ),
        'str(row.get("platforms") or "4")': (
            'str(row.get("platforms") or "0")'
        ),
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def _connect_replacement() -> str:
    return '''
def _connect(
    context: PolicyContext = PolicyContext.EXPLORER,
) -> duckdb.DuckDBPyConnection:
    connection = _connect_read_only(
        DB_PATH,
        label="warehouse database",
    )
    install_market_policy_view(
        connection,
        context,
    )
    return connection
'''.strip()


def _semantics_replacement() -> str:
    return '''
def _connect_semantics() -> duckdb.DuckDBPyConnection:
    if not os.path.exists(SEMANTICS_DB_PATH):
        raise FileNotFoundError(
            f"Semantic matcher database not found: "
            f"{SEMANTICS_DB_PATH}. "
            "Run build_semantics_separate_db.py first."
        )

    connection = _connect_read_only(
        SEMANTICS_DB_PATH,
        label="semantic matcher database",
    )
    install_semantic_policy_views(
        connection,
        PolicyContext.MATCHER,
    )
    return connection
'''.strip()


def _patch_exports(text: str) -> str:
    tree = ast.parse(text)
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (_route_path(node) or "").endswith("/export")
    ]
    for node in sorted(functions, key=lambda item: item.lineno, reverse=True):
        source = ast.get_source_segment(text, node)
        if source is None:
            continue
        updated = source.replace(
            "_connect()",
            "_connect(PolicyContext.EXPORT)",
        )
        if updated != source:
            text = _replace_node(text, node, updated)
    return text


def _patch_explorer(text: str) -> str:
    if MARKER in text:
        return text

    import_block = f'''
# {MARKER}
from api.source_policy import (
    PolicyContext,
    install_market_policy_view,
    install_semantic_policy_views,
)
'''
    text = _insert_import(
        text,
        after="from fastapi.templating import Jinja2Templates",
        block=import_block,
    )

    tree = ast.parse(text)
    text = _replace_node(
        text,
        _function(tree, "_connect"),
        _connect_replacement(),
    )

    tree = ast.parse(text)
    text = _replace_node(
        text,
        _function(tree, "_connect_semantics"),
        _semantics_replacement(),
    )

    return _patch_exports(text)


def _compile(path: Path, text: str) -> None:
    compile(text, str(path), "exec")


def main() -> int:
    for path in (MAIN_PATH, EXPLORER_PATH):
        if not path.exists():
            raise PatchError(
                f"Run from the project root. Missing: {path}"
            )

    main_text = _patch_main(MAIN_PATH.read_text(encoding="utf-8"))
    explorer_text = _patch_explorer(
        EXPLORER_PATH.read_text(encoding="utf-8")
    )

    _compile(MAIN_PATH, main_text)
    _compile(EXPLORER_PATH, explorer_text)

    _backup(MAIN_PATH)
    _backup(EXPLORER_PATH)

    MAIN_PATH.write_text(main_text, encoding="utf-8")
    EXPLORER_PATH.write_text(explorer_text, encoding="utf-8")

    print("Phase 15B source policy applied.")
    print(f"Updated: {MAIN_PATH}")
    print(f"Updated: {EXPLORER_PATH}")
    print("Backups use the suffix .phase15b.bak.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
