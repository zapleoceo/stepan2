"""app/connectors may not import back into the layers that import it.

app/api/_ui_html, _ui_panels, _ui_settings, _query and app/worker/main all import the registry,
which imports every connector module. One `from app.api._ui_panels import ...` inside a future
<kind>_ui.py closes that loop and the whole API package stops importing — at startup, on the
server. The rule was written as a sentence in app/connectors/__init__.py; a sentence is not a
guard, so this walks the package's own import statements instead.

Leaf helpers are fine: app.api._i18n holds the translation table and imports nothing from us.
"""
from __future__ import annotations

import ast
import pathlib

FORBIDDEN_PREFIXES = ("app.api._ui", "app.api._query", "app.api.ui", "app.worker")
_PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "app" / "connectors"


def _imported_modules(source: str, filename: str) -> list[str]:
    """Absolute dotted names this module imports, `from . import x` resolved to app.connectors.x."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            found.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # relative — inside app.connectors by construction
                base = f"app.connectors.{base}" if base else "app.connectors"
            found.append(base)
            found.extend(f"{base}.{a.name}" for a in node.names)
    return found


def test_no_connector_module_imports_the_layers_that_import_it() -> None:
    offenders: list[str] = []
    files = sorted(_PACKAGE.glob("*.py"))
    assert files, "the connectors package moved — this guard is pointing at nothing"
    for path in files:
        for name in _imported_modules(path.read_text(encoding="utf-8"), path.name):
            if name.startswith(FORBIDDEN_PREFIXES):
                offenders.append(f"{path.name} -> {name}")
    assert offenders == []
