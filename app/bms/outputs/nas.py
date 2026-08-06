"""NAS output.

Kept as the NAS-facing entry point; the generation itself is now generic and
lives in `portal.py`, driven by the declarative bindings. `SUPPORTED_KEYS` is the
set of templates wired end to end.
"""

from __future__ import annotations

from .bindings import ALL_BINDINGS
from .portal import BuiltRows, UnsupportedTemplate, build_rows, generate_workbook

# Every template with a binding and a value map is generable.
SUPPORTED_KEYS = tuple(binding.template_key for binding in ALL_BINDINGS)

__all__ = [
    "SUPPORTED_KEYS",
    "BuiltRows",
    "UnsupportedTemplate",
    "build_rows",
    "generate_workbook",
]
