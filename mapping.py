"""Canonical value -> per-template literal mapping.

The same fact is spelled differently in every insurer template. Gender is `Male`
in NAS, `MALE` in ADNIC, `M` in Daman and either in Sukoon. Relation is
`Principal` / `MEMBER` / `Principal` / `Employee`. Salary band is a full sentence
in NAS, an uppercase phrase in ADNIC, `1-4000` in Daman and `1` in Sukoon.

So the platform holds one canonical value per fact and maps it on the way out.
The maps are data. Adding an insurer is a new entry here plus a `TemplateSpec`,
not a change to any processing code.

Only NAS is populated in this phase, as agreed. The other insurers are declared
with their known literals so the shape is proven, and are marked incomplete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


class UnsupportedValue(Exception):
    """A canonical value the target template has no literal for."""


@dataclass(frozen=True)
class ValueMap:
    """Per-template literals for the controlled fields."""

    key: str
    gender: dict[str, str] = field(default_factory=dict)
    relation: dict[str, str] = field(default_factory=dict)
    marital_status: dict[str, str] = field(default_factory=dict)
    date_format: str = "%d-%m-%Y"
    # Values this insurer genuinely cannot express, with the reason. Writing an
    # approximation into a regulated submission is worse than refusing.
    unsupported: dict[str, dict[str, str]] = field(default_factory=dict)

    def render_date(self, iso_value: str | None) -> str | None:
        """Convert a stored ISO date into the template's expected text form."""
        if not iso_value:
            return None
        try:
            parsed = date.fromisoformat(iso_value)
        except ValueError:
            # Already in some other form; pass it through untouched rather than
            # silently discarding a value a user may have typed deliberately.
            return iso_value
        return parsed.strftime(self.date_format)

    def render(self, kind: str, value: str | None) -> str | None:
        """Translate a canonical value into this template's literal.

        A value the template's own dropdown cannot express raises, rather than
        being passed through and silently rejected by the insurer's import.
        """
        if not value:
            return None
        blocked = self.unsupported.get(kind, {})
        if value in blocked:
            raise UnsupportedValue(
                f"{self.key} cannot express {kind}={value!r}: {blocked[value]}"
            )
        table = getattr(self, kind, {})
        return table.get(value, value)


# NAS uses full title-case labels, and dates as DD-MM-YYYY.
NAS = ValueMap(
    key="nas",
    gender={"Male": "Male", "Female": "Female", "M": "Male", "F": "Female"},
    relation={
        "Principal": "Principal",
        "Spouse": "Spouse",
        "Child": "Child",
        "Parent": "Parent",
        "Others": "Others",
        "Ex-Spouse": "Ex-Spouse",
    },
    marital_status={
        "Single": "Single",
        "Married": "Married",
        "Divorced": "Divorced",
        "Widowed": "Widowed",
    },
    date_format="%d-%m-%Y",
)

# ADNIC uses uppercase throughout, and calls the principal MEMBER. Every literal
# below is taken from the workbook's own inline validation lists:
#   Gender     "MALE,FEMALE"
#   Dependency "MEMBER,SPOUSE,CHILD"
#   Marital    "SINGLE,MARRIED"
# The workbook offers no Parent or Ex-Spouse dependency, so those raise rather
# than being quietly bent into CHILD.
ADNIC = ValueMap(
    key="adnic",
    gender={"Male": "MALE", "Female": "FEMALE", "M": "MALE", "F": "FEMALE"},
    relation={"Principal": "MEMBER", "Employee": "MEMBER", "Spouse": "SPOUSE", "Child": "CHILD"},
    marital_status={"Single": "SINGLE", "Married": "MARRIED"},
    unsupported={
        "relation": {
            "Parent": "ADNIC's Dependency list offers only MEMBER, SPOUSE and CHILD.",
            "Ex-Spouse": "ADNIC's Dependency list offers only MEMBER, SPOUSE and CHILD.",
            "Others": "ADNIC's Dependency list offers only MEMBER, SPOUSE and CHILD.",
        },
        "marital_status": {
            "Divorced": "ADNIC's Marital list offers only SINGLE and MARRIED.",
            "Widowed": "ADNIC's Marital list offers only SINGLE and MARRIED.",
        },
    },
)

# Daman codes everything, per its List Values sheet:
#   GenderList  M / F        MaritalList  M / S
#   RelationList Principal, Spouse, Child, Parent
# Its instruction row states dates as DD/MM/YYYY.
DAMAN = ValueMap(
    key="daman",
    gender={"Male": "M", "Female": "F", "M": "M", "F": "F"},
    relation={"Principal": "Principal", "Spouse": "Spouse", "Child": "Child", "Parent": "Parent"},
    marital_status={"Single": "S", "Married": "M"},
    date_format="%d/%m/%Y",
    unsupported={
        "relation": {
            "Ex-Spouse": "Daman's RelationList offers Principal, Spouse, Child and Parent.",
            "Others": "Daman's RelationList offers Principal, Spouse, Child and Parent.",
        },
        "marital_status": {
            "Divorced": "Daman's MaritalList offers only M and S.",
            "Widowed": "Daman's MaritalList offers only M and S.",
        },
    },
)

# Sukoon's hidden Sheet2 lists both labels and codes for gender, relation and
# marital status; the labels are used, since the visible columns are validated
# against the label ranges. Its Relation list calls the principal Employee.
SUKOON = ValueMap(
    key="sukoon",
    gender={"Male": "Male", "Female": "Female", "M": "Male", "F": "Female"},
    relation={"Principal": "Employee", "Employee": "Employee", "Spouse": "Spouse", "Child": "Child"},
    marital_status={"Single": "Single", "Married": "Married"},
    unsupported={
        "relation": {
            "Parent": "Sukoon's relation list offers only Employee, Spouse and Child.",
            "Ex-Spouse": "Sukoon's relation list offers only Employee, Spouse and Child.",
            "Others": "Sukoon's relation list offers only Employee, Spouse and Child.",
        },
        "marital_status": {
            "Divorced": "Sukoon's marital list offers only Single and Married.",
            "Widowed": "Sukoon's marital list offers only Single and Married.",
        },
    },
)

# Template key -> value map. New insurers are registered here.
BY_TEMPLATE: dict[str, ValueMap] = {
    "nas.addition.aldar.v1": NAS,
    "nas.addition.iffco.v1": NAS,
    "nas.addition.hr.v1": NAS,
    "nas.deletion.v1": NAS,
    "adnic.enrolment.v1": ADNIC,
    "adnic.termination.v1": ADNIC,
    "daman.addition.v1": DAMAN,
    "sukoon.addition.v1": SUKOON,
}


def for_template(template_key: str) -> ValueMap:
    try:
        return BY_TEMPLATE[template_key]
    except KeyError:
        raise KeyError(
            f"no value map registered for template {template_key!r}; "
            "add one rather than writing raw values into a portal workbook"
        ) from None
