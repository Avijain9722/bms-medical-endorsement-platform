"""Every registered insurer template, generated end to end.

Each binding is checked against the real workbook: the columns it names must
exist, the literals it writes must be the ones that template's own dropdowns
accept, and the produced file must still match its structural fingerprint.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms.ooxml.package import M, OoxmlPackage  # noqa: E402
from bms.ooxml.sheet import sheet_part_names  # noqa: E402
from bms.outputs import portal  # noqa: E402
from bms.outputs.bindings import ALL_BINDINGS, BY_TEMPLATE  # noqa: E402
from bms.outputs.mapping import UnsupportedValue, for_template as value_map_for  # noqa: E402
from bms.templates.specs import SPECS_BY_KEY  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

ADDITION_KEYS = [b.template_key for b in ALL_BINDINGS if b.transaction == "addition"]
DELETION_KEYS = [b.template_key for b in ALL_BINDINGS if b.transaction == "deletion"]


def member(transaction="addition", **overrides):
    base = dict(
        id="m1",
        approved=True,
        transaction_type=transaction,
        first_name="Demo",
        middle_name=None,
        last_name="Testcase",
        date_of_birth="1988-04-12",
        gender="Female",
        marital_status="Single",
        nationality="United Kingdom",
        passport_no="X1234567",
        passport_expiry="2031-06-30",
        emirates_id="784-1988-1234567-1",
        unified_no="11223344",
        visa_file_number="201/2026/1234567",
        birth_certificate_number=None,
        staff_id="90001",
        relation="Principal",
        category="CAT B",
        contract_name="ACME ESTATES - L.L.C - AUH",
        effective_date=date.today().isoformat(),
        member_card_no="DEMO-CARD-0001",
        deletion_reason="Resignation",
        principal_card_no=None,
        email="hr@example.invalid",
        mobile_no="971500000000",
    )
    base.update(overrides)
    namespace = SimpleNamespace(**base)
    namespace.full_name = " ".join(
        p for p in (namespace.first_name, namespace.middle_name, namespace.last_name) if p
    )
    return namespace


def read_entry_sheet(path, template_key) -> tuple[dict, dict]:
    """Return (header row by column letter, populated cells by reference)."""
    spec = SPECS_BY_KEY[template_key]
    pkg = OoxmlPackage.open(path)
    parts = sheet_part_names(pkg.read("xl/workbook.xml"), pkg.read("xl/_rels/workbook.xml.rels"))
    shared = [
        "".join(t.text or "" for t in si.iter(M + "t"))
        for si in ET.fromstring(pkg.read("xl/sharedStrings.xml"))
    ]
    root = ET.fromstring(pkg.read(parts[spec.entry_sheet]))

    headers, values = {}, {}
    for row in root.findall(f"{M}sheetData/{M}row"):
        row_number = int(row.get("r"))
        for cell in row:
            node = cell.find(M + "v")
            if node is None:
                continue
            text = shared[int(node.text)] if cell.get("t") == "s" else node.text
            column = "".join(ch for ch in cell.get("r") if ch.isalpha())
            if row_number == spec.header_row:
                headers[column] = text
            elif row_number >= spec.first_data_row:
                values[cell.get("r")] = text
    return headers, values


def _by_header(headers: dict, values: dict) -> dict:
    """Populated cells keyed by their column heading rather than its letter."""
    result = {}
    for ref, value in values.items():
        column = "".join(ch for ch in ref if ch.isalpha())
        if column in headers:
            result[headers[column]] = value
    return result


# ------------------------------------------------------- binding integrity


@pytest.mark.parametrize("binding", ALL_BINDINGS, ids=lambda b: b.template_key)
def test_binding_only_names_columns_the_template_has(binding):
    spec = SPECS_BY_KEY[binding.template_key]
    unknown = set(binding.fields) - set(spec.columns)
    assert unknown == set(), f"{binding.template_key} maps unknown columns: {unknown}"


@pytest.mark.parametrize("binding", ALL_BINDINGS, ids=lambda b: b.template_key)
def test_intentionally_blank_columns_are_real_and_unmapped(binding):
    """A documented blank must be a real column, and must not also be filled."""
    spec = SPECS_BY_KEY[binding.template_key]
    for column, reason in binding.intentionally_blank.items():
        assert column in spec.columns, f"{binding.template_key}: {column} is not a template column"
        assert column not in binding.fields, f"{binding.template_key}: {column} is both blank and mapped"
        assert reason.strip(), f"{binding.template_key}: {column} has no stated reason"


@pytest.mark.parametrize("binding", ALL_BINDINGS, ids=lambda b: b.template_key)
def test_every_binding_has_a_value_map(binding):
    assert value_map_for(binding.template_key) is not None


# ------------------------------------------------------------ generation


@pytest.mark.parametrize("template_key", ADDITION_KEYS)
def test_addition_templates_generate_and_hold_their_structure(template_key, tmp_path):
    output = tmp_path / f"{template_key}{Path(SPECS_BY_KEY[template_key].source).suffix}"
    built = portal.generate_workbook(
        template_key, [member()], repo_root=REPO_ROOT, output=output
    )
    assert len(built.rows) == 1
    assert output.exists()


@pytest.mark.parametrize("template_key", DELETION_KEYS)
def test_deletion_templates_generate_and_hold_their_structure(template_key, tmp_path):
    output = tmp_path / f"{template_key}.xlsx"
    built = portal.generate_workbook(
        template_key, [member(transaction="deletion")], repo_root=REPO_ROOT, output=output
    )
    assert len(built.rows) == 1
    assert output.exists()


def test_adnic_writes_its_own_uppercase_literals(tmp_path):
    output = tmp_path / "adnic.xlsx"
    portal.generate_workbook(
        "adnic.enrolment.v1", [member()], repo_root=REPO_ROOT, output=output
    )
    headers, values = read_entry_sheet(output, "adnic.enrolment.v1")
    by_header = _by_header(headers, values)

    assert by_header["Gender"] == "FEMALE"
    assert by_header["Dependency"] == "MEMBER"
    assert by_header["Marital"] == "SINGLE"
    assert by_header["Country"] == "UAE"     # the column's only permitted value
    assert by_header["S.No"] == "1"


def test_daman_writes_codes_and_starts_at_row_three(tmp_path):
    output = tmp_path / "daman.xlsm"
    portal.generate_workbook(
        "daman.addition.v1", [member()], repo_root=REPO_ROOT, output=output
    )
    headers, values = read_entry_sheet(output, "daman.addition.v1")

    assert values["I3"] == "F"        # Gender
    assert values["N3"] == "S"        # Marital Status
    assert values["J3"] == "Principal"
    assert values["E3"] == "Emirates ID"   # National Id Type, derived
    assert values["H3"] == date.today().strftime("%d/%m/%Y")
    # Row 2 is the header row and must survive untouched.
    assert headers["B"] == "First Name"


def test_daman_national_id_type_follows_the_member(tmp_path):
    output = tmp_path / "daman2.xlsm"
    portal.generate_workbook(
        "daman.addition.v1",
        [member(emirates_id=None, unified_no="11223344")],
        repo_root=REPO_ROOT,
        output=output,
    )
    _, values = read_entry_sheet(output, "daman.addition.v1")
    assert values["E3"] == "UID No."


def test_sukoon_calls_the_principal_employee(tmp_path):
    output = tmp_path / "sukoon.xlsx"
    portal.generate_workbook(
        "sukoon.addition.v1", [member()], repo_root=REPO_ROOT, output=output
    )
    _, values = read_entry_sheet(output, "sukoon.addition.v1")
    assert values["I2"] == "Employee"
    assert values["G2"] == "Female"
    assert values["A2"] == "1"


def test_nas_keeps_its_own_title_case_and_date_format(tmp_path):
    output = tmp_path / "nas.xlsx"
    portal.generate_workbook(
        "nas.addition.aldar.v1", [member()], repo_root=REPO_ROOT, output=output
    )
    _, values = read_entry_sheet(output, "nas.addition.aldar.v1")
    assert values["J2"] == "Female"
    assert values["M2"] == "Principal"
    assert values["H2"] == date.today().strftime("%d-%m-%Y")


# --------------------------------------------------- refusing what cannot fit


def test_adnic_refuses_a_relation_its_dropdown_cannot_express():
    """A Parent cannot be written to ADNIC as CHILD just to make the row fit."""
    value_map = value_map_for("adnic.enrolment.v1")
    with pytest.raises(UnsupportedValue, match="MEMBER, SPOUSE and CHILD"):
        value_map.render("relation", "Parent")


def test_generation_surfaces_an_unsupported_value_rather_than_writing_it(tmp_path):
    with pytest.raises(UnsupportedValue):
        portal.generate_workbook(
            "adnic.enrolment.v1",
            [member(relation="Parent")],
            repo_root=REPO_ROOT,
            output=tmp_path / "adnic-bad.xlsx",
        )


def test_daman_refuses_divorced_because_its_list_has_two_values():
    value_map = value_map_for("daman.addition.v1")
    with pytest.raises(UnsupportedValue, match="only M and S"):
        value_map.render("marital_status", "Divorced")


def test_nas_accepts_the_full_marital_list():
    """NAS does offer all four, so nothing is refused there."""
    value_map = value_map_for("nas.addition.aldar.v1")
    for value in ("Single", "Married", "Divorced", "Widowed"):
        assert value_map.render("marital_status", value) == value


# ------------------------------------------------------------ housekeeping


def test_unapproved_members_are_reported_not_written(tmp_path):
    built = portal.build_rows("nas.addition.aldar.v1", [member(approved=False)])
    assert built.rows == []
    assert built.skipped and "not approved" in built.skipped[0][1]


def test_a_deletion_member_is_not_written_into_an_addition_template():
    built = portal.build_rows("nas.addition.aldar.v1", [member(transaction="deletion")])
    assert built.rows == []
    assert "not a addition" in built.skipped[0][1]


def test_blank_columns_are_reported_with_their_reasons():
    built = portal.build_rows("daman.addition.v1", [member()])
    assert "salary_band" in built.blank_columns
    assert "mapping" in built.blank_columns["salary_band"]


def test_unknown_template_is_refused():
    with pytest.raises(portal.UnsupportedTemplate):
        portal.build_rows("mednet.addition.v1", [member()])


def test_attachment_names_reach_every_template_that_has_the_column(tmp_path):
    attachments = {"m1": {"photo": "90001 - Photo.jpg", "declaration": "90001 - MAF.pdf"}}

    output = tmp_path / "nas-attach.xlsx"
    portal.generate_workbook(
        "nas.addition.aldar.v1", [member()], repo_root=REPO_ROOT,
        output=output, attachments=attachments,
    )
    _, values = read_entry_sheet(output, "nas.addition.aldar.v1")
    assert values["AK2"] == "90001 - Photo.jpg"
    assert values["AS2"] == "90001 - MAF.pdf"

    sukoon_output = tmp_path / "sukoon-attach.xlsx"
    portal.generate_workbook(
        "sukoon.addition.v1", [member()], repo_root=REPO_ROOT,
        output=sukoon_output, attachments=attachments,
    )
    _, sukoon_values = read_entry_sheet(sukoon_output, "sukoon.addition.v1")
    assert sukoon_values["X2"] == "90001 - Photo.jpg"
