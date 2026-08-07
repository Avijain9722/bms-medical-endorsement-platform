"""Generate a populated workbook from a registered template, then prove it held.

Every output starts as a fresh copy of the registered master. We write only into
the approved input rectangle, then recompute the structural fingerprint and diff
it against the baseline. A non-empty diff blocks release.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..ooxml import fingerprint
from ..ooxml.package import M, OoxmlPackage
from ..ooxml.sheet import SharedStrings, SheetWriter, register_namespaces, serialise, sheet_part_names
from ..templates.specs import (
    BMS_LOG,
    LOG_POST_SUBMISSION_FIELDS,
    LOG_PROTECTED_COLUMNS,
    TemplateSpec,
)


class StructuralDrift(Exception):
    """Raised when a generated workbook no longer matches its registered baseline."""

    def __init__(self, key: str, problems: list[str]):
        self.key = key
        self.problems = problems
        detail = "\n  - ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        super().__init__(f"structural drift in {key}:\n  - {detail}{more}")


class UnknownField(Exception):
    """Raised when a caller supplies a field the template has no column for."""


def master_path(spec: TemplateSpec, repo_root: str | Path) -> Path:
    return Path(repo_root) / spec.source


def baseline(spec: TemplateSpec, repo_root: str | Path) -> dict[str, Any]:
    """Structural fingerprint of the untouched master."""
    pkg = OoxmlPackage.open(master_path(spec, repo_root))
    header_rows, input_rows = spec.fingerprint_rows()
    return fingerprint.compute(pkg, header_rows=header_rows, input_rows=input_rows)


def _blank_entry_sheet(pkg: OoxmlPackage, spec: TemplateSpec, part: str) -> None:
    """Drop every data row from the entry sheet, keeping headers and structure.

    Used when registering a master that shipped with example data in it -- the BMS
    log arrived carrying 1,101 real member rows, which must never reach the
    platform or any generated file.
    """
    raw = pkg.read(part)
    register_namespaces(raw)
    root = ET.fromstring(raw)
    sheet_data = root.find(M + "sheetData")
    if sheet_data is None:
        return
    for row in list(sheet_data.findall(M + "row")):
        if int(row.get("r")) > spec.header_row:
            sheet_data.remove(row)
    dimension = root.find(M + "dimension")
    if dimension is not None:
        ref = dimension.get("ref") or "A1"
        start, _, end = ref.partition(":")
        if end:
            column = "".join(ch for ch in end if ch.isalpha())
            dimension.set("ref", f"{start}:{column}{spec.header_row}")
    pkg.write(part, serialise(root, raw))

    # A calc chain that references removed cells is stale; Excel rebuilds an empty
    # one on open. The part must stay present so the package structure is intact.
    if "xl/calcChain.xml" in pkg:
        pkg.write(
            "xl/calcChain.xml",
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
        )


def _resolve_columns(spec: TemplateSpec, row: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field_name, value in row.items():
        column = spec.columns.get(field_name)
        if column is None:
            raise UnknownField(
                f"{spec.key} has no column for field {field_name!r}; "
                "add it to the spec rather than guessing a destination"
            )
        if spec.key == BMS_LOG.key and column in LOG_PROTECTED_COLUMNS:
            raise UnknownField(
                f"{spec.key}: column {column} is a formula or helper cell and is never written"
            )
        values[column] = value
    return values


def generate(
    spec: TemplateSpec,
    rows: Iterable[Mapping[str, Any]],
    *,
    repo_root: str | Path,
    output: str | Path,
    blank_first: bool = False,
) -> Path:
    """Populate a fresh copy of the master and verify its structure survived."""
    source = master_path(spec, repo_root)
    pkg = OoxmlPackage.open(source)
    parts = sheet_part_names(pkg.read("xl/workbook.xml"), pkg.read("xl/_rels/workbook.xml.rels"))
    part = parts[spec.entry_sheet]

    header_rows, input_rows = spec.fingerprint_rows()

    # Capture the input row's cell styles before anything is removed. They carry
    # the template's number formats -- text columns, dd/mm/yyyy date columns --
    # and every member row we write inherits them.
    prototypes = SheetWriter(pkg.read(part), SharedStrings(pkg.read("xl/sharedStrings.xml"))).style_prototypes(
        spec.first_data_row
    )

    if blank_first:
        _blank_entry_sheet(pkg, spec, part)

    # The registered master is the workbook as it will be reused, so the baseline
    # is taken after any example data has been stripped.
    base = fingerprint.compute(pkg, header_rows=header_rows, input_rows=input_rows)

    shared = SharedStrings(pkg.read("xl/sharedStrings.xml"))
    writer = SheetWriter(pkg.read(part), shared)
    if not prototypes:
        prototypes = writer.style_prototypes(spec.first_data_row)

    for offset, row in enumerate(rows):
        writer.write_row(
            spec.first_data_row + offset,
            _resolve_columns(spec, row),
            prototypes=prototypes,
            template_row=spec.first_data_row,
        )

    writer.update_dimension()
    pkg.write(part, writer.to_bytes())
    pkg.write("xl/sharedStrings.xml", shared.to_bytes())

    output = Path(output)
    pkg.save(output)

    problems = verify(spec, output, base)
    problems += _style_problems(output, spec, prototypes, writer.created, writer.existing_styles)
    if problems:
        output.unlink(missing_ok=True)
        raise StructuralDrift(spec.key, problems)
    return output


def _style_problems(
    output: Path,
    spec: TemplateSpec,
    prototypes: Mapping[str, str],
    created: set[str],
    existing: Mapping[str, str | None],
) -> list[str]:
    """Check the formatting of everything we wrote.

    Cells we created must carry the template's prototype style for their column --
    that is what keeps Emirates ID columns text-formatted instead of collapsing to
    numbers, and date columns on the template's own date format. Cells that already
    existed must keep the style the template gave them, since several masters style
    their data rows individually.
    """
    if not created and not existing:
        return []
    pkg = OoxmlPackage.open(output)
    parts = sheet_part_names(pkg.read("xl/workbook.xml"), pkg.read("xl/_rels/workbook.xml.rels"))
    raw = pkg.read(parts[spec.entry_sheet])
    register_namespaces(raw)
    root = ET.fromstring(raw)

    problems = []
    for row in root.findall(f"{M}sheetData/{M}row"):
        for cell in row:
            ref = cell.get("r")
            actual = cell.get("s")
            if ref in created:
                column = "".join(ch for ch in ref if ch.isalpha())
                expected = prototypes.get(column)
                if expected is not None and actual != expected:
                    problems.append(
                        f"style at {ref}: new cell expected prototype {expected}, got {actual}"
                    )
            elif ref in existing and actual != existing[ref]:
                problems.append(
                    f"style at {ref}: template style {existing[ref]} was changed to {actual}"
                )
    return problems


def verify(
    spec: TemplateSpec,
    candidate: str | Path,
    base: dict[str, Any],
) -> list[str]:
    """Diff a generated workbook's structure against the registered baseline."""
    pkg = OoxmlPackage.open(candidate)
    header_rows, input_rows = spec.fingerprint_rows()
    current = fingerprint.compute(pkg, header_rows=header_rows, input_rows=input_rows)
    return fingerprint.diff(base, current)


def assert_blank_post_submission(spec: TemplateSpec, rows: Iterable[Mapping[str, Any]]) -> None:
    """Guard the log fields that must stay empty until staff act.

    A caller may pass these keys explicitly with an explicit value once the user
    has performed the corresponding workflow action; what is forbidden is the
    platform filling them in automatically with a placeholder.
    """
    if spec.key != BMS_LOG.key:
        return
    for row in rows:
        for field_name in LOG_POST_SUBMISSION_FIELDS & set(row):
            value = row[field_name]
            if value in ("N/A", "NA", "Pending", "-", 0, "0"):
                raise ValueError(
                    f"{field_name} must be genuinely blank until recorded, got {value!r}"
                )
