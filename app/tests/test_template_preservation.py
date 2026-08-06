"""Template-preservation regression suite.

These tests run against the real supplied workbooks. They are the gate that keeps
the platform honest about its central promise: a generated portal file differs
from its master only in the member rows we deliberately wrote.
"""

from __future__ import annotations

import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms.ooxml import fingerprint  # noqa: E402
from bms.ooxml.package import OoxmlPackage  # noqa: E402
from bms.ooxml.sheet import column_index, column_name  # noqa: E402
from bms.registry.generate import (  # noqa: E402
    UnknownField,
    assert_blank_post_submission,
    baseline,
    generate,
)
from bms.templates.specs import (  # noqa: E402
    ALL_SPECS,
    BMS_LOG,
    DAMAN_ADDITION,
    LOG_PROTECTED_COLUMNS,
    NAS_ALDAR_ADDITION,
    NAS_DELETION,
    NAS_HR_ADDITION,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def sample_rows(spec, count=3):
    """Synthetic member rows covering every mapped column of a template."""
    rows = []
    for i in range(count):
        row = {}
        for field_name in spec.columns:
            if spec.key == BMS_LOG.key and field_name in {"sr_no"}:
                row[field_name] = i + 1
            else:
                row[field_name] = f"{field_name}-{i}"
        rows.append(row)
    return rows


# ------------------------------------------------------------------ helpers


def test_column_helpers_round_trip():
    for index in (1, 26, 27, 52, 53, 83, 703):
        assert column_index(column_name(index)) == index
    assert column_name(1) == "A"
    assert column_name(27) == "AA"
    assert column_index("AW") == 49
    assert column_index("CE") == 83


# ------------------------------------------------------- discovery assertions
# These pin the structural facts the specs were built from. If a supplied
# workbook is ever swapped for a different version, these fail loudly rather
# than letting the platform write into the wrong columns.


def test_nas_variants_share_one_layout():
    layouts = {}
    for spec in (NAS_ALDAR_ADDITION, NAS_HR_ADDITION):
        fp = baseline(spec, REPO_ROOT)
        entry = next(s for s in fp["sheets"] if s["name"] == spec.entry_sheet)
        layouts[spec.key] = entry["headers"]
    aldar, nashr = layouts.values()
    assert aldar == nashr, "NAS addition variants must share an identical header row"
    assert len(aldar) == 49


def test_nashr_hides_columns_that_aldar_does_not():
    aldar = baseline(NAS_ALDAR_ADDITION, REPO_ROOT)
    nashr = baseline(NAS_HR_ADDITION, REPO_ROOT)

    def hidden(fp, sheet):
        entry = next(s for s in fp["sheets"] if s["name"] == sheet)
        return {c for lo, hi in entry["hidden_columns"] for c in range(lo, hi + 1)}

    assert hidden(aldar, "Sample Template") == set()
    assert hidden(nashr, "Sample Template") == {
        column_index(c) for c in ("C", "E", "F", "G", "N", "O", "P", "T")
    }


def test_daman_protection_and_macros_are_present_in_the_master():
    fp = baseline(DAMAN_ADDITION, REPO_ROOT)
    assert fp["has_vba"] is True
    assert fp["workbook_protection"]["lockStructure"] == "1"
    member_details = next(s for s in fp["sheets"] if s["name"] == "Member Details")
    assert member_details["protection"]["algorithmName"] == "SHA-512"
    assert member_details["header_row"] == 2
    assert len(fp["tables"]) == 18


def test_log_spec_maps_no_field_onto_a_formula_column():
    """TAT, Current Date, Pending-since, Aged Pending and the AC-AP feeders."""
    mapped = set(BMS_LOG.columns.values())
    assert mapped & LOG_PROTECTED_COLUMNS == set()


def test_adding_a_formula_column_to_the_map_is_rejected_at_write_time():
    """Second line of defence if someone later extends the spec by mistake."""
    unsafe = replace(BMS_LOG, columns={**BMS_LOG.columns, "tat": "U"})
    with pytest.raises(UnknownField, match="formula or helper"):
        generate(unsafe, [{"tat": 5}], repo_root=REPO_ROOT, output="/dev/null")


def test_unknown_field_is_rejected_rather_than_dropped():
    with pytest.raises(UnknownField, match="no column for field"):
        generate(
            NAS_DELETION,
            [{"not_a_real_field": "x"}],
            repo_root=REPO_ROOT,
            output="/dev/null",
        )


# ------------------------------------------------- the core preservation gate


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.key)
def test_generated_workbook_matches_registered_fingerprint(spec, tmp_path):
    """Zero structural delta between master and generated output."""
    base = baseline(spec, REPO_ROOT)
    suffix = Path(spec.source).suffix
    output = tmp_path / f"{spec.key}{suffix}"

    generate(
        spec,
        sample_rows(spec),
        repo_root=REPO_ROOT,
        output=output,
        blank_first=(spec.key == BMS_LOG.key),
    )

    pkg = OoxmlPackage.open(output)
    header_rows, input_rows = spec.fingerprint_rows()
    current = fingerprint.compute(pkg, header_rows=header_rows, input_rows=input_rows)
    assert fingerprint.diff(base, current) == []


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.key)
def test_master_workbook_is_never_modified(spec, tmp_path):
    source = REPO_ROOT / spec.source
    before = source.read_bytes()
    generate(
        spec,
        sample_rows(spec),
        repo_root=REPO_ROOT,
        output=tmp_path / "out" / Path(spec.source).name,
        blank_first=(spec.key == BMS_LOG.key),
    )
    assert source.read_bytes() == before


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.key)
def test_every_part_survives_generation(spec, tmp_path):
    """No OOXML part is added, dropped or reordered."""
    output = tmp_path / Path(spec.source).name
    generate(
        spec,
        sample_rows(spec),
        repo_root=REPO_ROOT,
        output=output,
        blank_first=(spec.key == BMS_LOG.key),
    )
    with zipfile.ZipFile(REPO_ROOT / spec.source) as before, zipfile.ZipFile(output) as after:
        assert before.namelist() == after.namelist()


def test_daman_macro_and_labels_are_byte_identical(tmp_path):
    """The one workbook that carries a locked VBA project and a Purview label."""
    output = tmp_path / "daman.xlsm"
    generate(DAMAN_ADDITION, sample_rows(DAMAN_ADDITION), repo_root=REPO_ROOT, output=output)

    before = OoxmlPackage.open(REPO_ROOT / DAMAN_ADDITION.source)
    after = OoxmlPackage.open(output)
    for part in (
        "xl/vbaProject.bin",
        "docProps/custom.xml",
        "xl/ctrlProps/ctrlProp1.xml",
        "xl/ctrlProps/ctrlProp4.xml",
        "xl/printerSettings/printerSettings1.bin",
        "xl/drawings/vmlDrawing1.vml",
    ):
        assert before.part_sha256(part) == after.part_sha256(part), part

    for part in (f"xl/tables/table{i}.xml" for i in range(1, 19)):
        assert before.part_sha256(part) == after.part_sha256(part), part


def test_daman_writes_start_at_row_three(tmp_path):
    """Row 1 is instruction text and row 2 is the header -- both must survive."""
    output = tmp_path / "daman.xlsm"
    generate(DAMAN_ADDITION, sample_rows(DAMAN_ADDITION, count=2), repo_root=REPO_ROOT, output=output)

    values = _entry_sheet_values(output, DAMAN_ADDITION)
    assert values["A1"].startswith("Mandatory")
    assert values["B2"] == "First Name"
    assert values["B3"] == "first_name-0"
    assert values["B4"] == "first_name-1"


def test_nas_header_row_survives_and_data_starts_at_row_two(tmp_path):
    output = tmp_path / "nas.xlsx"
    generate(NAS_ALDAR_ADDITION, sample_rows(NAS_ALDAR_ADDITION, count=2), repo_root=REPO_ROOT, output=output)

    values = _entry_sheet_values(output, NAS_ALDAR_ADDITION)
    assert values["A1"] == "Contract Name"
    assert values["H1"] == "Effective Date "  # trailing space is load-bearing
    assert values["AW1"] == "Waived PEC  Declaration"  # double space is too
    assert values["A2"] == "contract_name-0"
    assert values["A3"] == "contract_name-1"


# --------------------------------------------------------- BMS log specifics


def test_log_example_rows_are_stripped_at_registration(tmp_path):
    """The supplied log ships with 1,101 real member rows. None may survive."""
    output = tmp_path / "log.xlsx"
    generate(BMS_LOG, [], repo_root=REPO_ROOT, output=output, blank_first=True)

    values = _entry_sheet_values(output, BMS_LOG)
    data_cells = {ref: v for ref, v in values.items() if int(_row_of(ref)) > 1}
    assert data_cells == {}, "no example data may reach a generated log"
    assert values["A1"] == "SR NO."
    assert values["V1"] == "Saiba Voucher No."


def test_log_post_submission_columns_stay_genuinely_blank(tmp_path):
    output = tmp_path / "log.xlsx"
    generate(
        BMS_LOG,
        [
            {
                "sr_no": 1,
                "shared_by": "JAHNVI",
                "client_name": "IFFCO",
                "insurer": "QATAR INSURANCE",
                "beneficiary_name": "Test Member",
                "relation": "PRINCIPAL",
                "entry_type": "ADDITION",
                "status": "PENDING TO INSURER",
            }
        ],
        repo_root=REPO_ROOT,
        output=output,
        blank_first=True,
    )

    values = _entry_sheet_values(output, BMS_LOG)
    # G, Q, R, T, V and W are the six post-submission columns.
    for ref in ("G2", "Q2", "R2", "T2", "V2", "W2"):
        assert ref not in values, f"{ref} must be an empty cell, not a placeholder"
    assert values["H2"] == "Test Member"


def test_placeholder_values_are_refused_for_post_submission_fields():
    for bad in ("N/A", "Pending", "-", 0):
        with pytest.raises(ValueError, match="genuinely blank"):
            assert_blank_post_submission(BMS_LOG, [{"card_no": bad}])


def test_log_controlled_vocabularies_survive(tmp_path):
    """Edit Data Validation drives every dropdown on MAIN DATA."""
    output = tmp_path / "log.xlsx"
    generate(BMS_LOG, [], repo_root=REPO_ROOT, output=output, blank_first=True)
    fp = fingerprint.compute(
        OoxmlPackage.open(output), header_rows={"MAIN DATA": 1}, input_rows={"MAIN DATA": 2}
    )
    main = next(s for s in fp["sheets"] if s["name"] == "MAIN DATA")
    sources = {v["f1"] for v in main["validations"]}
    assert "'Edit Data Validation'!$G$3:$G$22" in sources  # Status
    assert "'Edit Data Validation'!$A$3:$A$20" in sources  # SHARED BY


# ------------------------------------------------------------------ fixtures


def _row_of(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isdigit())


def _entry_sheet_values(path, spec) -> dict[str, str]:
    """Read back the entry sheet as {cell ref: value}."""
    import xml.etree.ElementTree as ET

    from bms.ooxml.package import M
    from bms.ooxml.sheet import sheet_part_names

    pkg = OoxmlPackage.open(path)
    parts = sheet_part_names(pkg.read("xl/workbook.xml"), pkg.read("xl/_rels/workbook.xml.rels"))
    sst = [
        "".join(t.text or "" for t in si.iter(M + "t"))
        for si in ET.fromstring(pkg.read("xl/sharedStrings.xml"))
    ]
    root = ET.fromstring(pkg.read(parts[spec.entry_sheet]))
    values = {}
    for row in root.findall(f"{M}sheetData/{M}row"):
        for cell in row:
            v = cell.find(M + "v")
            if v is None:
                continue
            if cell.get("t") == "s":
                values[cell.get("r")] = sst[int(v.text)]
            else:
                values[cell.get("r")] = v.text
    return values
