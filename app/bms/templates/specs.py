"""Registered template descriptors, derived from inspection of the supplied files.

Every value here was read out of the workbooks themselves, not assumed. Column
letters, header rows and first input rows differ per insurer -- notably Daman,
which carries instruction text on row 1, headers on row 2 and its first member on
row 3, with a pre-seeded Salary Band value already sitting in AK3.

Nothing in this module may be edited to "fix" a template. Where a supplied
workbook contains a defect (empty Department/Grade lookups, a Sub-Nationality
range pointing at a blank cell, a Work Region validation that starts one row
late), it is recorded as-is and preserved on output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Engines -------------------------------------------------------------------
# "ooxml"  -- surgical part rewrite; preserves macros, protection and labels.
# "recalc" -- ooxml write followed by an Excel pass on Windows so calculated
#             columns and dynamic-array formulas evaluate before hand-off.
ENGINE_OOXML = "ooxml"
ENGINE_RECALC = "recalc"


@dataclass(frozen=True)
class TemplateSpec:
    key: str
    source: str
    entry_sheet: str
    header_row: int
    first_data_row: int
    columns: dict[str, str]
    engine: str = ENGINE_OOXML
    header_rows: dict[str, int] = field(default_factory=dict)
    input_rows: dict[str, int] = field(default_factory=dict)
    notes: str = ""

    def fingerprint_rows(self) -> tuple[dict[str, int], dict[str, int]]:
        header_rows = {self.entry_sheet: self.header_row, **self.header_rows}
        input_rows = {self.entry_sheet: self.first_data_row, **self.input_rows}
        return header_rows, input_rows


# --------------------------------------------------------------------- NAS
# All three NAS addition variants share one 49-column layout (A-AW). They differ
# only in hidden-column visibility and in the hidden Contract/Category lookup
# sheets, so one column map serves all of them.
NAS_ADDITION_COLUMNS = {
    "contract_name": "A",
    "first_name": "B",
    "middle_name": "C",
    "last_name": "D",
    "arabic_first_name": "E",
    "arabic_middle_name": "F",
    "arabic_last_name": "G",
    "effective_date": "H",
    "date_of_birth": "I",
    "gender": "J",
    "marital_status": "K",
    "category": "L",
    "relation": "M",
    "department": "N",
    "grade": "O",
    "principal_card_no": "P",
    "family_no": "Q",
    "staff_id": "R",
    "nationality": "S",
    "sub_nationality": "T",
    "emirates_id": "U",
    "unified_no": "V",
    "passport_no": "W",
    "work_country": "X",
    "work_emirate": "Y",
    "work_region": "Z",
    "residence_country": "AA",
    "residence_emirate": "AB",
    "residence_region": "AC",
    "email": "AD",
    "mobile_no": "AE",
    "salary_band": "AF",
    "commission": "AG",
    "visa_issuance_emirate": "AH",
    "birth_certificate_number": "AI",
    "visa_file_number": "AJ",
    "member_photo": "AK",
    "member_type": "AL",
    "occupation": "AM",
    "regulator_no": "AN",
    "passport_expiry_date": "AO",
    "visa_expiry_date": "AP",
    "coc_available": "AQ",
    "pec_declaration": "AR",
    "medical_declaration_file": "AS",
    "visa_type": "AT",
    "company_phone": "AU",
    "company_mail": "AV",
    "waived_pec_declaration": "AW",
}

NAS_ALDAR_ADDITION = TemplateSpec(
    key="nas.addition.aldar.v1",
    source="02_PORTAL_TEMPLATES/NAS- ALDAR- Addition RequestTemplate.xlsx",
    entry_sheet="Sample Template",
    header_row=1,
    first_data_row=2,
    columns=NAS_ADDITION_COLUMNS,
    notes=(
        "13 contracts, 6 categories in the hidden lookup sheets. No hidden columns "
        "on the entry sheet, unlike the NASHR variant. Departments and Grades "
        "lookups are empty by design -- those columns stay blank."
    ),
)

NAS_IFFCO_ADDITION = TemplateSpec(
    key="nas.addition.iffco.v1",
    source="02_PORTAL_TEMPLATES/NAS- IFFCO- Addition RequestTemplate.xlsx",
    entry_sheet="Sample Template",
    header_row=1,
    first_data_row=2,
    columns=NAS_ADDITION_COLUMNS,
    notes="4 IFFCO contracts, 5 categories (CAT I/J/L, NE and DXB splits).",
)

NAS_HR_ADDITION = TemplateSpec(
    key="nas.addition.hr.v1",
    source="02_PORTAL_TEMPLATES/NASHRRequestTemplateNew(1).xlsx",
    entry_sheet="Sample Template",
    header_row=1,
    first_data_row=2,
    columns=NAS_ADDITION_COLUMNS,
    notes=(
        "Hides C, E, F, G, N, O, P and T. The hidden columns remain part of the "
        "upload structure and are written when a value exists."
    ),
)

NAS_DELETION = TemplateSpec(
    key="nas.deletion.v1",
    source="02_PORTAL_TEMPLATES/NAS DeleteEmployees.xlsx",
    entry_sheet="Sample Template",
    header_row=1,
    first_data_row=2,
    columns={
        "member_card_no": "A",
        "emirates_id": "B",
        "effective_date": "C",
        "deletion_reason": "D",
    },
    notes=(
        "Header Explanation states either card number or Emirates ID is required. "
        "Deletion Reason is a fixed list of 7 values; anything the client's wording "
        "does not match maps to Others."
    ),
)

# ------------------------------------------------------------------- ADNIC
ADNIC_ENROLMENT = TemplateSpec(
    key="adnic.enrolment.v1",
    source="02_PORTAL_TEMPLATES/ADNIC MemberEnrollment Form.xlsx",
    entry_sheet="Member Enrollment",
    header_row=1,
    first_data_row=2,
    columns={
        "serial_no": "A",
        "pf_no": "B",
        "member_no": "C",
        "member_name": "D",
        "date_of_birth": "E",
        "gender": "F",
        "dependency": "G",
        "marital_status": "H",
        "effective_date": "I",
        "previous_coverage_or_entry_date": "J",
        "change_of_status_date": "K",
        "emirates_id": "L",
        "eid_application_no": "M",
        "entry_permit_or_file_no": "N",
        "visa_type": "O",
        "visa_emirate": "P",
        "class_no": "Q",
        "nationality": "R",
        "passport_no": "S",
        "uid_no": "T",
        "mobile_no": "U",
        "email": "V",
        "thiqa_card_no": "W",
        "position": "X",
        "work_location": "Y",
        "residence_location": "Z",
        "member_category": "AA",
        "salary_band": "AB",
        "commission": "AC",
        "birth_certificate_id": "AD",
        "sponsor_type": "AE",
        "sponsor_id": "AF",
        "country": "AG",
        "height_cm": "AH",
        "weight_kg": "AI",
    },
    notes=(
        "Columns AO and AP are hidden helper lists (nationalities, Dubai work "
        "locations) that feed validations on the same sheet -- never written. "
        "The 'WorkLocation' defined name is already broken (#REF!) in the source."
    ),
)

ADNIC_TERMINATION = TemplateSpec(
    key="adnic.termination.v1",
    source="02_PORTAL_TEMPLATES/ADNIC MemberTermination Form.xlsx",
    entry_sheet="Member Termination",
    header_row=1,
    first_data_row=2,
    columns={
        "serial_no": "A",
        "member_no": "B",
        "member_type": "C",
        "date_of_termination": "D",
        "reason_for_termination": "E",
    },
    notes=(
        "Hidden column F holds a 188-entry nationality list no validation "
        "references; it is dead data that is still preserved byte-for-byte. "
        "Reason for Termination has no dropdown -- free text."
    ),
)

# ------------------------------------------------------------------ Sukoon
SUKOON_ADDITION = TemplateSpec(
    key="sukoon.addition.v1",
    source="02_PORTAL_TEMPLATES/Sukoon Member-addition sheet.xlsx",
    entry_sheet="Members",
    header_row=1,
    first_data_row=2,
    columns={
        "serial_no": "A",
        "first_name": "B",
        "middle_name": "C",
        "last_name": "D",
        "employee_number": "E",
        "date_of_birth": "F",
        "gender": "G",
        "marital_status": "H",
        "relation": "I",
        "category": "J",
        "region": "K",
        "lsb": "L",
        "nationality": "M",
        "passport_no": "N",
        "emirates_id": "O",
        "uid_no": "P",
        "visa_issued_location": "Q",
        "actual_salary_band": "R",
        "person_commission": "S",
        "residential_location": "T",
        "work_location": "U",
        "mobile_no": "V",
        "email": "W",
        "photo_file_name": "X",
        "sponsor_type": "Y",
        "sponsor_id": "Z",
        "sponsor_contact_number": "AA",
        "sponsor_contact_email": "AB",
        "occupation": "AC",
        "effective_date": "AD",
        "visa_file_number": "AE",
        "birth_certificate_number": "AF",
    },
    notes=(
        "Sukoon uses codes, not labels: Sponsor Type 1-5, Salary Band 1-4, LSB 1-2, "
        "Relation Employee/Spouse/Child. Photo File Name must be unique in the file "
        "and match the supplied JPGs."
    ),
)

# ------------------------------------------------------------------- Daman
# Row 1 is per-column instruction text, row 2 is the header, row 3 is the first
# member. Only the visible A-AN block is written; the hidden AO-CE back-office
# block is left blank. The macro project, both SHA-512 sheet protections, the 18
# tables, the form controls and the Purview/RightsWATCH labels are preserved.
DAMAN_ADDITION = TemplateSpec(
    key="daman.addition.v1",
    source="02_PORTAL_TEMPLATES/DAMAN Addition Template.xlsm",
    entry_sheet="Member Details",
    header_row=2,
    first_data_row=3,
    engine=ENGINE_RECALC,
    columns={
        "picture_file_name": "A",
        "first_name": "B",
        "middle_name": "C",
        "last_name": "D",
        "national_id_type": "E",
        "emirates_id": "F",
        "date_of_birth": "G",
        "insurance_effective_date": "H",
        "gender": "I",
        "relation": "J",
        "principal_reference": "K",
        "staff_number": "L",
        "plan": "M",
        "marital_status": "N",
        "arabic_first_name": "O",
        "arabic_middle_name": "P",
        "arabic_last_name": "Q",
        "member_number": "R",
        "nationality": "S",
        "department": "T",
        "country_of_residency": "U",
        "occupation": "V",
        "city": "W",
        "labor_id": "X",
        "previous_insurance_coverage": "Y",
        "place_of_visa_issuance": "Z",
        "visa_unified_number": "AA",
        "gross_salary": "AB",
        "accommodation_provided": "AC",
        "commission": "AD",
        "passport_no": "AE",
        "emirate": "AF",
        "residential_location": "AG",
        "work_location": "AH",
        "email": "AI",
        "contact_number": "AJ",
        "salary_band": "AK",
        "sponsor_uid_type": "AL",
        "sponsor_uid": "AM",
    },
    header_rows={"List Values": 1, "Cover sheet": 1, "AD Census": 1},
    input_rows={"List Values": 2, "Cover sheet": 2, "AD Census": 2},
    notes=(
        "Macro-enabled and locked. AK3 arrives pre-seeded with '1-4000'; that cell "
        "is overwritten by the member's own salary band. 'Is Valid' (AN) is filled "
        "by the workbook's own macro on the BMS workstation, never by the platform."
    ),
)

# --------------------------------------------------------------- BMS log
# Columns A-AA are operational; AC-AP are helper/feeder columns driving SUMMARY
# and are never written. The six post-submission columns stay blank until staff
# perform the corresponding action.
BMS_LOG = TemplateSpec(
    key="bms.log.2026",
    source="03_INTERNAL_LOG_TEMPLATE/New Log Format -2026.xlsx",
    entry_sheet="MAIN DATA",
    header_row=1,
    first_data_row=2,
    engine=ENGINE_RECALC,
    columns={
        "sr_no": "A",
        "shared_by": "B",
        "client_name": "C",
        "sub_group": "D",
        "insurer": "E",
        "policy_no": "F",
        "request_ref_no": "G",
        "beneficiary_name": "H",
        "relation": "I",
        "category": "J",
        "staff_id": "K",
        "emirates_id": "L",
        "entry_type": "M",
        "effective_date": "N",
        "status": "O",
        "request_receive_date": "P",
        "request_sent_date_to_insurer": "Q",
        "card_no": "R",
        "remarks": "S",
        "card_receive_and_sent_date": "T",
        "saiba_voucher_no": "V",
        "bbm_invoice_date": "W",
        "remarks2": "X",
    },
    header_rows={"SUMMARY": 3, "Edit Data Validation": 2, "Required Docs": 1},
    input_rows={"SUMMARY": 4, "Edit Data Validation": 4, "Required Docs": 2},
    notes=(
        "U (TAT), Y (Current Date), Z (Pending with Insurer since), AA (Aged "
        "Pending) and AC-AP are formula cells and are excluded from the column map "
        "so they can never be overwritten. The supplied workbook's 1,101 example "
        "rows are stripped at registration; the platform starts from an empty log."
    ),
)

# Columns that must never receive an automatic value on the BMS log. They are
# populated only when a user performs the corresponding explicit action.
LOG_POST_SUBMISSION_FIELDS = frozenset(
    {
        "request_ref_no",
        "request_sent_date_to_insurer",
        "card_no",
        "card_receive_and_sent_date",
        "saiba_voucher_no",
        "bbm_invoice_date",
    }
)

# Formula and helper columns on MAIN DATA. Writing into any of these is a bug.
LOG_PROTECTED_COLUMNS = frozenset(
    {"U", "Y", "Z", "AA", "AC", "AD", "AE", "AF", "AG", "AH", "AI", "AJ", "AK", "AL", "AM", "AN", "AO", "AP"}
)

ALL_SPECS: tuple[TemplateSpec, ...] = (
    NAS_ALDAR_ADDITION,
    NAS_IFFCO_ADDITION,
    NAS_HR_ADDITION,
    NAS_DELETION,
    ADNIC_ENROLMENT,
    ADNIC_TERMINATION,
    SUKOON_ADDITION,
    DAMAN_ADDITION,
    BMS_LOG,
)

SPECS_BY_KEY = {spec.key: spec for spec in ALL_SPECS}
