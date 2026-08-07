"""On-demand reports about the registered templates.

Run these when you want evidence rather than a pass/fail: what the platform
believes each workbook looks like, and proof that generating from it changes
nothing structural.

    python3 -m tools.reports inventory
    python3 -m tools.reports preservation
    python3 -m tools.reports all --out report.md --samples out/

Output is Markdown so it renders directly in a GitHub Actions job summary.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms.ooxml import fingerprint  # noqa: E402
from bms.ooxml.package import OoxmlPackage  # noqa: E402
from bms.ooxml.sheet import column_name  # noqa: E402
from bms.registry.generate import StructuralDrift, baseline, generate  # noqa: E402
from bms.templates.specs import ALL_SPECS, BMS_LOG, DAMAN_ADDITION, TemplateSpec  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# Parts that carry macros, controls and classification labels. If any of these
# change during generation the workbook is no longer the one the insurer issued.
DAMAN_CRITICAL_PARTS = (
    "xl/vbaProject.bin",
    "docProps/custom.xml",
    "xl/ctrlProps/ctrlProp1.xml",
    "xl/ctrlProps/ctrlProp2.xml",
    "xl/ctrlProps/ctrlProp3.xml",
    "xl/ctrlProps/ctrlProp4.xml",
    "xl/drawings/vmlDrawing1.vml",
    *(f"xl/tables/table{i}.xml" for i in range(1, 19)),
)


def _hidden_columns(sheet: dict) -> str:
    letters = [
        column_name(c) for lo, hi in sheet["hidden_columns"] for c in range(lo, hi + 1)
    ]
    if not letters:
        return "none"
    if len(letters) > 12:
        return f"{', '.join(letters[:12])} … ({len(letters)} total)"
    return ", ".join(letters)


def _sheet_list(fp: dict) -> str:
    """Sheets in workbook order, marking the hidden ones."""
    parts = []
    for sheet in fp["sheets"]:
        suffix = "" if sheet["state"] == "visible" else " *(hidden)*"
        parts.append(f"`{sheet['name']}`{suffix}")
    return ", ".join(parts)


def _sample_rows(spec: TemplateSpec, count: int) -> list[dict]:
    rows = []
    for i in range(count):
        row = {}
        for field_name in spec.columns:
            row[field_name] = (i + 1) if field_name == "sr_no" else f"{field_name}-{i + 1}"
        rows.append(row)
    return rows


# ---------------------------------------------------------------- inventory


def inventory() -> str:
    lines = [
        "## Template inventory",
        "",
        "What the registry believes each supplied workbook looks like. Every value is",
        "read from the workbook itself at report time, not from a stored copy.",
        "",
        "| Template | File type | Engine | Sheets (hidden) | Header row | First data row | Fields | Macros | Workbook lock |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    details = []

    for spec in ALL_SPECS:
        fp = baseline(spec, REPO_ROOT)
        hidden_sheets = sum(1 for s in fp["sheets"] if s["state"] != "visible")
        lines.append(
            f"| `{spec.key}` "
            f"| {Path(spec.source).suffix} "
            f"| {spec.engine} "
            f"| {len(fp['sheets'])} ({hidden_sheets}) "
            f"| {spec.header_row} "
            f"| {spec.first_data_row} "
            f"| {len(spec.columns)} "
            f"| {'yes' if fp['has_vba'] else 'no'} "
            f"| {'yes' if fp['workbook_protection'] else 'no'} |"
        )

        entry = next(s for s in fp["sheets"] if s["name"] == spec.entry_sheet)
        block = [
            "",
            f"### `{spec.key}`",
            "",
            f"Source: `{spec.source}`",
            "",
            "| Property | Value |",
            "| --- | --- |",
            f"| Entry sheet | `{spec.entry_sheet}` |",
            f"| Sheets in order | {_sheet_list(fp)} |",
            f"| Hidden columns on entry sheet | {_hidden_columns(entry)} |",
            f"| Data validations on entry sheet | {len(entry['validations'])} |",
            f"| Defined names | {len(fp['defined_names'])} |",
            f"| Tables | {len(fp['tables'])} |",
            f"| Entry sheet protection | {'yes' if entry['protection'] else 'no'} |",
            f"| Merged ranges on entry sheet | {len(entry['merge_cells'])} |",
            f"| OOXML parts | {len(fp['parts'])} |",
            f"| Fingerprint digest | `{fingerprint.digest(fp)[:16]}…` |",
        ]
        if spec.notes:
            block += ["", f"> {spec.notes}"]
        details += block

    return "\n".join(lines + details) + "\n"


# ------------------------------------------------------------- preservation


def preservation(sample_count: int, samples_dir: Path | None) -> tuple[str, bool]:
    """Generate from every master and report the structural delta."""
    lines = [
        "## Preservation report",
        "",
        f"Each template is populated with {sample_count} synthetic member row(s) from a fresh",
        "copy of its master, then re-fingerprinted. A passing row means the generated",
        "workbook differs from the master only in the cells we deliberately wrote.",
        "",
        "| Template | Result | Parts before | Parts after | Structural deltas |",
        "| --- | --- | --- | --- | --- |",
    ]
    failures = []
    work_dir = samples_dir or Path("/tmp/bms-preservation")
    work_dir.mkdir(parents=True, exist_ok=True)

    for spec in ALL_SPECS:
        source = REPO_ROOT / spec.source
        output = work_dir / f"{spec.key}{Path(spec.source).suffix}"
        before = len(zipfile.ZipFile(source).namelist())
        try:
            generate(
                spec,
                _sample_rows(spec, sample_count),
                repo_root=REPO_ROOT,
                output=output,
                blank_first=(spec.key == BMS_LOG.key),
            )
        except StructuralDrift as drift:
            failures.append((spec.key, drift.problems))
            lines.append(
                f"| `{spec.key}` | **FAILED** | {before} | - | {len(drift.problems)} |"
            )
            continue

        after = len(zipfile.ZipFile(output).namelist())
        lines.append(f"| `{spec.key}` | PASSED | {before} | {after} | 0 |")

    lines += [
        "",
        "### Macro and label integrity (Daman)",
        "",
        "The only supplied workbook with a locked VBA project, SHA-512 sheet protection",
        "and a Microsoft Purview sensitivity label. Each part below is compared",
        "byte-for-byte between the master and the generated file.",
        "",
    ]
    daman_output = work_dir / f"{DAMAN_ADDITION.key}.xlsm"
    if daman_output.exists():
        master = OoxmlPackage.open(REPO_ROOT / DAMAN_ADDITION.source)
        produced = OoxmlPackage.open(daman_output)
        mismatched = [
            part
            for part in DAMAN_CRITICAL_PARTS
            if master.part_sha256(part) != produced.part_sha256(part)
        ]
        if mismatched:
            failures.append((DAMAN_ADDITION.key, [f"part changed: {p}" for p in mismatched]))
            lines.append(f"**FAILED** -- {len(mismatched)} part(s) changed: {', '.join(mismatched)}")
        else:
            lines.append(
                f"**PASSED** -- all {len(DAMAN_CRITICAL_PARTS)} critical parts byte-identical "
                "(VBA project, Purview label, 4 form controls, VML drawing, 18 tables)."
            )
    else:
        lines.append("Not run -- the Daman workbook failed to generate.")

    if failures:
        lines += ["", "### Failures", ""]
        for key, problems in failures:
            lines.append(f"**`{key}`**")
            lines.append("")
            for problem in problems[:25]:
                lines.append(f"- {problem}")
            if len(problems) > 25:
                lines.append(f"- … and {len(problems) - 25} more")
            lines.append("")

    return "\n".join(lines) + "\n", not failures


# --------------------------------------------------------------- blank fields


def blank_fields() -> tuple[str, bool]:
    """Prove the log's post-submission columns come out genuinely empty."""
    import xml.etree.ElementTree as ET

    from bms.ooxml.package import M
    from bms.ooxml.sheet import sheet_part_names

    work_dir = Path("/tmp/bms-blank-check")
    work_dir.mkdir(parents=True, exist_ok=True)
    output = work_dir / "log.xlsx"

    generate(
        BMS_LOG,
        [
            {
                "sr_no": 1,
                "shared_by": "REPORT",
                "client_name": "SAMPLE",
                "insurer": "QATAR INSURANCE",
                "beneficiary_name": "Sample Member",
                "relation": "PRINCIPAL",
                "entry_type": "ADDITION",
                "status": "PENDING TO INSURER",
            }
        ],
        repo_root=REPO_ROOT,
        output=output,
        blank_first=True,
    )

    pkg = OoxmlPackage.open(output)
    parts = sheet_part_names(pkg.read("xl/workbook.xml"), pkg.read("xl/_rels/workbook.xml.rels"))
    root = ET.fromstring(pkg.read(parts["MAIN DATA"]))
    present = {
        cell.get("r")
        for row in root.findall(f"{M}sheetData/{M}row")
        for cell in row
        if cell.find(M + "v") is not None
    }

    expected_blank = {
        "G2": "Request Ref.No.",
        "Q2": "Request sent date to Insurer",
        "R2": "CARD #",
        "T2": "Card receive and sent date to Insured",
        "V2": "Saiba Voucher No.",
        "W2": "BBM Invoice date",
    }

    lines = [
        "## Post-submission blank-field report",
        "",
        "These BMS log columns must contain a genuinely empty cell until a user records",
        "the corresponding event. Never `N/A`, `Pending`, `-` or `0`.",
        "",
        "| Cell | Column | Result |",
        "| --- | --- | --- |",
    ]
    ok = True
    for ref, label in expected_blank.items():
        blank = ref not in present
        ok &= blank
        lines.append(f"| `{ref}` | {label} | {'blank' if blank else '**NOT BLANK**'} |")

    lines += ["", f"Row count written: 1. Result: {'**PASSED**' if ok else '**FAILED**'}."]
    return "\n".join(lines) + "\n", ok


# ---------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report",
        choices=["all", "inventory", "preservation", "blank-fields"],
        help="which report to produce",
    )
    parser.add_argument("--out", help="also write the Markdown to this file")
    parser.add_argument(
        "--samples",
        help="keep the generated sample workbooks in this directory",
    )
    parser.add_argument("--rows", type=int, default=3, help="synthetic rows per template")
    args = parser.parse_args(argv)

    samples_dir = Path(args.samples) if args.samples else None
    sections: list[str] = []
    ok = True

    if args.report in ("all", "inventory"):
        sections.append(inventory())
    if args.report in ("all", "preservation"):
        text, passed = preservation(args.rows, samples_dir)
        sections.append(text)
        ok &= passed
    if args.report in ("all", "blank-fields"):
        text, passed = blank_fields()
        sections.append(text)
        ok &= passed

    report = "\n".join(sections)
    print(report)
    if args.out:
        Path(args.out).write_text(report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
