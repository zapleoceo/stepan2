"""Connector specs — one declarative object per channel kind, plus the registry.

Import the registry, not the individual modules: `from app.connectors.registry import
spec_for, all_specs`. Nothing in this package may import app/api/_ui_* or app/worker/* —
both of those import the registry."""
