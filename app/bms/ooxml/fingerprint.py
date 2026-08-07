"""Structural fingerprinting for registered portal templates.

A fingerprint captures everything about a workbook that must not change when the
platform populates it, and deliberately excludes the things that legitimately do
change when member rows are written (cell values, sheet dimension, calc chain).

Registration stores the baseline. Generation recomputes it on the produced file
and diffs. Any delta blocks release -- see `bms.registry.verify`.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from typing import Any

from .package import M, R, X14, XM, OoxmlPackage

# Parts whose content changes as a normal consequence of writing member rows.
# They are excluded from the part-digest map but their *presence* is still checked.
VOLATILE_PARTS = frozenset({"xl/calcChain.xml", "docProps/app.xml", "docProps/core.xml"})


def _text(el: ET.Element | None) -> str | None:
    return None if el is None else (el.text or "")


def _shared_strings(pkg: OoxmlPackage) -> list[str]:
    if "xl/sharedStrings.xml" not in pkg:
        return []
    root = ET.fromstring(pkg.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(M + "t")) for si in root]


def _cell_value(cell: ET.Element, sst: list[str]) -> str | None:
    inline = cell.find(M + "is")
    if inline is not None:
        return "".join(t.text or "" for t in inline.iter(M + "t"))
    v = cell.find(M + "v")
    if v is None:
        return None
    if cell.get("t") == "s":
        try:
            return sst[int(v.text)]
        except (ValueError, IndexError, TypeError):
            return v.text

    return v.text


def _sheet_targets(pkg: OoxmlPackage) -> list[dict[str, Any]]:
    """Sheets in workbook order, with visibility and the part each resolves to."""
    wb = ET.fromstring(pkg.read("xl/workbook.xml"))
    rels = ET.fromstring(pkg.read("xl/_rels/workbook.xml.rels"))
    target_by_id = {rel.get("Id"): rel.get("Target") for rel in rels}
    sheets = []
    for sheet in wb.findall(f"{M}sheets/{M}sheet"):
        target = target_by_id[sheet.get(R + "id")]
        part = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
        sheets.append(
            {
                "name": sheet.get("name"),
                "sheet_id": sheet.get("sheetId"),
                "state": sheet.get("state") or "visible",
                "part": part,
            }
        )
    return sheets


def _hidden_columns(root: ET.Element) -> list[list[int]]:
    cols = root.find(M + "cols")
    if cols is None:
        return []
    return [
        [int(c.get("min")), int(c.get("max"))]
        for c in cols
        if c.get("hidden") == "1"
    ]


def _validations(root: ET.Element) -> list[dict[str, Any]]:
    """Inline and x14-extension data validations, in a stable order."""
    out = []
    for dv in root.findall(f"{M}dataValidations/{M}dataValidation"):
        out.append(
            {
                "ext": False,
                "sqref": dv.get("sqref"),
                "type": dv.get("type"),
                "operator": dv.get("operator"),
                "allow_blank": dv.get("allowBlank"),
                "show_dropdown": dv.get("showDropDown"),
                "f1": _text(dv.find(M + "formula1")),
                "f2": _text(dv.find(M + "formula2")),
            }
        )
    for dv in root.iter(X14 + "dataValidation"):
        f1 = dv.find(X14 + "formula1")
        sqref = dv.find(XM + "sqref")
        out.append(
            {
                "ext": True,
                "sqref": _text(sqref),
                "type": dv.get("type"),
                "operator": dv.get("operator"),
                "allow_blank": dv.get("allowBlank"),
                "show_dropdown": dv.get("showDropDown"),
                "f1": "".join(f1.itertext()) if f1 is not None else None,
                "f2": None,
            }
        )
    out.sort(key=lambda d: (d["ext"], d["sqref"] or "", d["f1"] or ""))
    return out


def _protection(root: ET.Element) -> dict[str, str] | None:
    sp = root.find(M + "sheetProtection")
    return None if sp is None else dict(sorted(sp.attrib.items()))


def _header_row(root: ET.Element, sst: list[str], row_number: int) -> dict[str, str]:
    for row in root.findall(f"{M}sheetData/{M}row"):
        if int(row.get("r")) != row_number:
            continue
        cells = {}
        for cell in row:
            value = _cell_value(cell, sst)
            if value is not None and value != "":
                cells[cell.get("r")] = value
        return cells
    return {}


def input_row_styles(root: ET.Element, row_number: int) -> dict[str, str]:
    """Style index per column letter on a given row.

    Deliberately *not* part of the structural fingerprint: a registered master may
    legitimately have no input row at all (the BMS log is registered blank), so
    this is data-dependent. Style preservation is checked separately, by comparing
    each written cell against the prototype captured from the template.
    """
    for row in root.findall(f"{M}sheetData/{M}row"):
        if int(row.get("r")) != row_number:
            continue
        styles = {}
        for cell in row:
            if cell.get("s") is not None:
                column = "".join(ch for ch in cell.get("r") if ch.isalpha())
                styles[column] = cell.get("s")
        return styles
    return {}


def _tables(pkg: OoxmlPackage) -> list[dict[str, Any]]:
    tables = []
    for name in pkg.names:
        if not name.startswith("xl/tables/") or not name.endswith(".xml"):
            continue
        t = ET.fromstring(pkg.read(name))
        tables.append(
            {
                "part": name,
                "name": t.get("name"),
                "display_name": t.get("displayName"),
                "ref": t.get("ref"),
                "header_row_count": t.get("headerRowCount"),
                "columns": [c.get("name") for c in t.iter(M + "tableColumn")],
            }
        )
    tables.sort(key=lambda d: d["part"])
    return tables


def _defined_names(pkg: OoxmlPackage) -> list[dict[str, Any]]:
    wb = ET.fromstring(pkg.read("xl/workbook.xml"))
    names = [
        {
            "name": dn.get("name"),
            "local_sheet_id": dn.get("localSheetId"),
            "hidden": dn.get("hidden"),
            "target": dn.text or "",
        }
        for dn in wb.findall(f"{M}definedNames/{M}definedName")
    ]
    names.sort(key=lambda d: (d["name"] or "", d["local_sheet_id"] or ""))
    return names


def compute(
    pkg: OoxmlPackage,
    *,
    header_rows: dict[str, int] | None = None,
    input_rows: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build the structural fingerprint of a workbook.

    `header_rows` and `input_rows` map sheet name -> row number. They differ per
    template: most portal sheets carry headers on row 1 with input from row 2,
    while the Daman workbook puts instruction text on row 1, headers on row 2 and
    the first input row on row 3.
    """
    header_rows = header_rows or {}
    input_rows = input_rows or {}
    sst = _shared_strings(pkg)
    wb = ET.fromstring(pkg.read("xl/workbook.xml"))
    protection = wb.find(M + "workbookProtection")

    sheets = []
    for sheet in _sheet_targets(pkg):
        root = ET.fromstring(pkg.read(sheet["part"]))
        header_row = header_rows.get(sheet["name"], 1)
        sheets.append(
            {
                "name": sheet["name"],
                "sheet_id": sheet["sheet_id"],
                "state": sheet["state"],
                "header_row": header_row,
                "headers": _header_row(root, sst, header_row),
                "hidden_columns": _hidden_columns(root),
                "validations": _validations(root),
                "protection": _protection(root),
                "merge_cells": sorted(
                    m.get("ref") for m in root.findall(f"{M}mergeCells/{M}mergeCell")
                ),
                "table_parts": len(root.findall(f"{M}tableParts/{M}tablePart")),
            }
        )

    return {
        "version": 1,
        "parts": sorted(pkg.names),
        "part_digests": {
            name: pkg.part_sha256(name)
            for name in sorted(pkg.names)
            if name.startswith(("xl/vbaProject", "xl/ctrlProps/", "customXml/"))
            or name in {"docProps/custom.xml", "xl/styles.xml", "xl/theme/theme1.xml"}
        },
        "has_vba": "xl/vbaProject.bin" in pkg,
        "workbook_protection": dict(sorted(protection.attrib.items()))
        if protection is not None
        else None,
        "defined_names": _defined_names(pkg),
        "tables": _tables(pkg),
        "sheets": sheets,
    }


def digest(fingerprint: dict[str, Any]) -> str:
    """Stable hash of a fingerprint, for cheap equality checks and storage."""
    payload = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def diff(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    """Human-readable structural deltas. Empty list means the structure held."""
    problems: list[str] = []

    def compare(path: str, a: Any, b: Any) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a:
                    problems.append(f"{path}.{key}: added ({b[key]!r})")
                elif key not in b:
                    problems.append(f"{path}.{key}: removed (was {a[key]!r})")
                else:
                    compare(f"{path}.{key}", a[key], b[key])
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                problems.append(f"{path}: length {len(a)} -> {len(b)}")
            for i, (x, y) in enumerate(zip(a, b)):
                compare(f"{path}[{i}]", x, y)
        elif a != b:
            problems.append(f"{path}: {a!r} -> {b!r}")

    compare("fingerprint", baseline, candidate)
    return problems
