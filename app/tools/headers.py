#!/usr/bin/env python3
"""Print a workbook's sheets, and one sheet's headers with their column letters.

A helper for registering a NEW insurer template. It reads the supplied workbook
and prints the column map in the shape `TemplateSpec.columns` wants, so the
letters come from the file itself rather than from counting columns by eye.

    python3 tools/headers.py "../02_PORTAL_TEMPLATES/Some Insurer.xlsx"
    python3 tools/headers.py "../02_PORTAL_TEMPLATES/Some Insurer.xlsx" Members 1

Read-only. It never writes to the supplied file.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms.ooxml.fingerprint import compute  # noqa: E402
from bms.ooxml.package import OoxmlPackage  # noqa: E402
from bms.ooxml.sheet import split_ref  # noqa: E402


def slug(text: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in text.strip().lower())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2

    path = Path(argv[0])
    sheet = argv[1] if len(argv) > 1 else None
    row = int(argv[2]) if len(argv) > 2 else 1

    fingerprint = compute(
        OoxmlPackage.open(path), header_rows={sheet: row} if sheet else None
    )

    print("SHEETS")
    for entry in fingerprint["sheets"]:
        state = entry.get("state") or "visible"
        print(f"   {entry['name']:28} {state}")

    if not sheet:
        print("\nPass a sheet name and header row to see its columns.")
        return 0

    target = next((s for s in fingerprint["sheets"] if s["name"] == sheet), None)
    if target is None:
        print(f"\nno sheet named {sheet!r}", file=sys.stderr)
        return 1

    header = target.get("headers", {})
    print(f"\nHEADERS on {sheet!r} row {row} -- paste into TemplateSpec.columns")
    print(f"({len(header)} columns)\n")
    # The fingerprint keys headers by cell reference; the spec wants the column
    # letter alone.
    columns = {split_ref(ref)[0]: text for ref, text in header.items()}
    for column, text in sorted(columns.items(), key=lambda kv: (len(kv[0]), kv[0])):
        print(f'    "{slug(text)}": "{column}",'.ljust(46) + f"# {text.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
