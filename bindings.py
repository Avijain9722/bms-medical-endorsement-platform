"""Declarative member-to-template bindings.

One insurer per dictionary. Adding a template means adding a `TemplateBinding`
and a `ValueMap`; no processing code changes, which is what "additional templates
without rewriting the platform" has to mean in practice.

Every literal here was read out of the supplied workbook's own validation lists
during discovery -- ADNIC's `MALE`/`FEMALE`, Daman's `M`/`F`, Sukoon's numeric
sponsor codes. Where a workbook offers a coded list whose *meaning* it never
states, the binding is deliberately absent and the field is left for BMS to
configure or a reviewer to enter. Guessing a code would put a wrong value in a
regulated submission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..models import Member, TransactionType

# How a single template column is filled.
#   attr     -- copy a member attribute verbatim
#   date     -- render a stored ISO date in the template's date format
#   gender / relation / marital -- translate through the template's ValueMap
#   const    -- a fixed literal the template requires
#   serial   -- the member's 1-based position in the file
#   photo / declaration -- the filename chosen by the document packager
#   derive   -- a small pure function of the member
BindingKind = str


@dataclass(frozen=True)
class Binding:
    kind: BindingKind
    source: str | None = None
    constant: str | None = None
    derive: Callable[[Member], object] | None = None


def attr(source: str) -> Binding:
    return Binding("attr", source=source)


def as_date(source: str) -> Binding:
    return Binding("date", source=source)


def gender(source: str = "gender") -> Binding:
    return Binding("gender", source=source)


def relation(source: str = "relation") -> Binding:
    return Binding("relation", source=source)


def marital(source: str = "marital_status") -> Binding:
    return Binding("marital", source=source)


def const(value: str) -> Binding:
    return Binding("const", constant=value)


def serial() -> Binding:
    return Binding("serial")


def photo() -> Binding:
    return Binding("photo")


def declaration() -> Binding:
    return Binding("declaration")


def derive(fn: Callable[[Member], object]) -> Binding:
    return Binding("derive", derive=fn)


@dataclass(frozen=True)
class TemplateBinding:
    template_key: str
    transaction: str
    value_map_key: str
    fields: dict[str, Binding] = field(default_factory=dict)
    # Columns the template exposes that BMS has instructed to leave blank, or
    # whose value cannot be derived without a rule BMS has not supplied. Recorded
    # so the reason survives in one place rather than as an absence.
    intentionally_blank: dict[str, str] = field(default_factory=dict)
    notes: str = ""


def _full_name(member: Member) -> str:
    return member.full_name


def _national_id_type(member: Member) -> str | None:
    """Daman's National Id Type, from which identifier the member actually has.

    The workbook's own list is `UID No.` / `Emirates ID`, and its row-1
    instruction says the application number applies to new entrants. This reads
    the member rather than assuming.
    """
    if member.emirates_id:
        return "Emirates ID"
    if member.unified_no:
        return "UID No."
    return None


# --------------------------------------------------------------------- NAS

NAS_ADDITION_FIELDS: dict[str, Binding] = {
    "contract_name": attr("contract_name"),
    "first_name": attr("first_name"),
    "middle_name": attr("middle_name"),
    "last_name": attr("last_name"),
    "effective_date": as_date("effective_date"),
    "date_of_birth": as_date("date_of_birth"),
    "gender": gender(),
    "marital_status": marital(),
    "category": attr("category"),
    "relation": relation(),
    "principal_card_no": attr("principal_card_no"),
    "staff_id": attr("staff_id"),
    "nationality": attr("nationality"),
    "emirates_id": attr("emirates_id"),
    "unified_no": attr("unified_no"),
    "passport_no": attr("passport_no"),
    "email": attr("email"),
    "mobile_no": attr("mobile_no"),
    "visa_file_number": attr("visa_file_number"),
    "birth_certificate_number": attr("birth_certificate_number"),
    "passport_expiry_date": as_date("passport_expiry"),
    "member_photo": photo(),
    "medical_declaration_file": declaration(),
}

NAS_ADDITION_BLANK = {
    "department": "BMS confirmed Department is left blank; the lookup sheet is empty.",
    "grade": "BMS confirmed Grade is left blank; the lookup sheet is empty.",
    "occupation": "Not mandatory for the NAS process; BMS confirmed it may stay blank.",
    "sub_nationality": "The template's non-UAE range points at a blank cell.",
    "regulator_no": "No BMS rule defines this field.",
    "commission": "No BMS rule defines this field.",
}


def _nas_addition(key: str) -> TemplateBinding:
    return TemplateBinding(
        template_key=key,
        transaction=TransactionType.ADDITION.value,
        value_map_key="nas",
        fields=dict(NAS_ADDITION_FIELDS),
        intentionally_blank=dict(NAS_ADDITION_BLANK),
    )


NAS_DELETION = TemplateBinding(
    template_key="nas.deletion.v1",
    transaction=TransactionType.DELETION.value,
    value_map_key="nas",
    fields={
        "member_card_no": attr("member_card_no"),
        "emirates_id": attr("emirates_id"),
        "effective_date": as_date("effective_date"),
        "deletion_reason": attr("deletion_reason"),
    },
    notes="Either card number or Emirates ID is required, per the workbook's own guidance.",
)

# ------------------------------------------------------------------- ADNIC

ADNIC_ENROLMENT = TemplateBinding(
    template_key="adnic.enrolment.v1",
    transaction=TransactionType.ADDITION.value,
    value_map_key="adnic",
    fields={
        "serial_no": serial(),
        "pf_no": attr("staff_id"),
        "member_name": derive(_full_name),
        "date_of_birth": as_date("date_of_birth"),
        "gender": gender(),
        "dependency": relation(),
        "marital_status": marital(),
        "effective_date": as_date("effective_date"),
        "emirates_id": attr("emirates_id"),
        "entry_permit_or_file_no": attr("visa_file_number"),
        "nationality": attr("nationality"),
        "passport_no": attr("passport_no"),
        "uid_no": attr("unified_no"),
        "mobile_no": attr("mobile_no"),
        "email": attr("email"),
        "member_category": attr("category"),
        "birth_certificate_id": attr("birth_certificate_number"),
        # The workbook validates this column to the single literal "UAE".
        "country": const("UAE"),
    },
    intentionally_blank={
        "member_no": "Assigned by ADNIC; blank on a new enrolment.",
        "eid_application_no": "Only for new entrants; entered by a reviewer when it applies.",
        "change_of_status_date": "Only for a change-in-status visa; entered by a reviewer.",
        "previous_coverage_or_entry_date": "No BMS precedence rule supplied for this date.",
        "visa_type": "The list mixes visa type and entrant status; no BMS mapping supplied.",
        "visa_emirate": "Not currently held on the member record.",
        "class_no": "Plan class is client-specific; no BMS mapping supplied.",
        "thiqa_card_no": "Applies to UAE nationals only; entered by a reviewer.",
        "position": "Occupation is not captured for this process.",
        "work_location": "Required only for Dubai-visa members; no BMS default supplied.",
        "residence_location": "Required only for Dubai-visa members; no BMS default supplied.",
        "salary_band": "Category-to-salary-band mapping has not been supplied by BMS.",
        "commission": "No BMS rule defines this field.",
        "sponsor_type": "Sponsor classification is not captured on the member record.",
        "sponsor_id": "Depends on sponsor type; not captured.",
        "height_cm": "Not collected by this process.",
        "weight_kg": "Not collected by this process.",
    },
    notes=(
        "The Dubai-conditional location and salary fields stay blank until BMS supplies "
        "the category mapping; the template does not mark them red, but ADNIC treats them "
        "as mandatory for Dubai-visa members."
    ),
)

ADNIC_TERMINATION = TemplateBinding(
    template_key="adnic.termination.v1",
    transaction=TransactionType.DELETION.value,
    value_map_key="adnic",
    fields={
        "serial_no": serial(),
        "member_no": attr("member_card_no"),
        "date_of_termination": as_date("effective_date"),
        "reason_for_termination": attr("deletion_reason"),
    },
    intentionally_blank={
        "member_type": "The column accepts a single character; no BMS mapping supplied.",
    },
    notes="Reason for Termination is free text in this workbook -- there is no dropdown.",
)

# ------------------------------------------------------------------ Sukoon

SUKOON_ADDITION = TemplateBinding(
    template_key="sukoon.addition.v1",
    transaction=TransactionType.ADDITION.value,
    value_map_key="sukoon",
    fields={
        "serial_no": serial(),
        "first_name": attr("first_name"),
        "middle_name": attr("middle_name"),
        "last_name": attr("last_name"),
        "employee_number": attr("staff_id"),
        "date_of_birth": as_date("date_of_birth"),
        "gender": gender(),
        "marital_status": marital(),
        "relation": relation(),
        "category": attr("category"),
        "nationality": attr("nationality"),
        "passport_no": attr("passport_no"),
        "emirates_id": attr("emirates_id"),
        "uid_no": attr("unified_no"),
        "mobile_no": attr("mobile_no"),
        "email": attr("email"),
        "effective_date": as_date("effective_date"),
        "visa_file_number": attr("visa_file_number"),
        "birth_certificate_number": attr("birth_certificate_number"),
        "photo_file_name": photo(),
    },
    intentionally_blank={
        "region": "Region is a three-value list; BMS has not mapped clients to it.",
        "lsb": "The workbook offers codes 1 and 2 without stating what they mean.",
        "actual_salary_band": "Codes 1-4 with no stated meaning, and no BMS mapping.",
        "person_commission": "No BMS rule defines this field.",
        "visa_issued_location": "Not currently held on the member record.",
        "residential_location": "Required for Dubai members; no BMS default supplied.",
        "work_location": "Required for Dubai members; no BMS default supplied.",
        "sponsor_type": "Codes 1-5 are explained in a cell comment, but the member record "
                        "does not capture sponsor classification.",
        "sponsor_id": "Depends on sponsor type; not captured.",
        "sponsor_contact_number": "Not captured.",
        "sponsor_contact_email": "Not captured.",
        "occupation": "Not captured for this process.",
    },
    notes=(
        "Sukoon uses numeric codes for Salary Band, LSB and Sponsor Type. Only the sponsor "
        "codes are documented, in cell comments; the rest are left for BMS to configure."
    ),
)

# ------------------------------------------------------------------- Daman

DAMAN_ADDITION = TemplateBinding(
    template_key="daman.addition.v1",
    transaction=TransactionType.ADDITION.value,
    value_map_key="daman",
    fields={
        "first_name": attr("first_name"),
        "middle_name": attr("middle_name"),
        "last_name": attr("last_name"),
        "national_id_type": derive(_national_id_type),
        "emirates_id": attr("emirates_id"),
        "date_of_birth": as_date("date_of_birth"),
        "insurance_effective_date": as_date("effective_date"),
        "gender": gender(),
        "relation": relation(),
        "principal_reference": attr("principal_card_no"),
        "staff_number": attr("staff_id"),
        "marital_status": marital(),
        "nationality": attr("nationality"),
        "visa_unified_number": attr("unified_no"),
        "passport_no": attr("passport_no"),
        "email": attr("email"),
        "contact_number": attr("mobile_no"),
        "picture_file_name": photo(),
    },
    intentionally_blank={
        "plan": "Daman plan names are client-specific; BMS confirmed they stay generic "
                "for now, so a reviewer selects the plan.",
        "member_number": "Existing Daman members only.",
        "department": "The workbook wants 'ND' when there is no department; BMS has not "
                      "confirmed that default.",
        "country_of_residency": "No BMS default supplied.",
        "city": "Mandatory for Daman but not captured on the member record.",
        "labor_id": "Not captured.",
        "place_of_visa_issuance": "Not currently held on the member record.",
        "commission": "No BMS rule defines this field.",
        "emirate": "Mandatory for Dubai visa holders; no BMS default supplied.",
        "residential_location": "Mandatory for Dubai visa holders; no BMS default supplied.",
        "work_location": "Mandatory for Dubai visa holders; no BMS default supplied.",
        "salary_band": "The workbook pre-seeds AK3 with 1-4000; BMS has not supplied the "
                       "category-to-salary-band mapping, so this is left for review.",
        "sponsor_uid_type": "Not captured.",
        "sponsor_uid": "Not captured.",
        "occupation": "Not captured for this process.",
        "arabic_first_name": "Arabic names are not captured.",
        "arabic_middle_name": "Arabic names are not captured.",
        "arabic_last_name": "Arabic names are not captured.",
        "previous_insurance_coverage": "COC handling is a separate BMS workflow.",
        "gross_salary": "Not captured; salary band is the reported figure.",
        "accommodation_provided": "Not captured.",
    },
    notes=(
        "Row 1 is instruction text and row 2 is the header, so member rows start at row 3. "
        "The 'Is Valid' column is filled by the workbook's own macro on the BMS workstation, "
        "never by the platform."
    ),
)


ALL_BINDINGS: tuple[TemplateBinding, ...] = (
    _nas_addition("nas.addition.aldar.v1"),
    _nas_addition("nas.addition.iffco.v1"),
    _nas_addition("nas.addition.hr.v1"),
    NAS_DELETION,
    ADNIC_ENROLMENT,
    ADNIC_TERMINATION,
    SUKOON_ADDITION,
    DAMAN_ADDITION,
)

BY_TEMPLATE: dict[str, TemplateBinding] = {b.template_key: b for b in ALL_BINDINGS}


def for_template(template_key: str) -> TemplateBinding:
    try:
        return BY_TEMPLATE[template_key]
    except KeyError:
        raise KeyError(
            f"no binding registered for template {template_key!r}; add one to "
            "bms/outputs/bindings.py rather than writing values into a portal workbook"
        ) from None
