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


@dataclass(frozen=True)
class ValueMap:
    """Per-template literals for the controlled fields."""

    key: str
    gender: dict[str, str] = field(default_factory=dict)
    relation: dict[str, str] = field(default_factory=dict)
    marital_status: dict[str, str] = field(default_factory=dict)
    date_format: str = "%d-%m-%Y"
    complete: bool = True

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
        if not value:
            return None
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

# Declared for shape only -- these templates are out of scope for phase 1 and
# their literals have not been confirmed against a completed example.
ADNIC = ValueMap(
    key="adnic",
    gender={"Male": "MALE", "Female": "FEMALE"},
    relation={"Principal": "MEMBER", "Spouse": "SPOUSE", "Child": "CHILD"},
    marital_status={"Single": "SINGLE", "Married": "MARRIED"},
    complete=False,
)

DAMAN = ValueMap(
    key="daman",
    gender={"Male": "M", "Female": "F"},
    relation={"Principal": "Principal", "Spouse": "Spouse", "Child": "Child", "Parent": "Parent"},
    marital_status={"Single": "S", "Married": "M"},
    date_format="%d/%m/%Y",
    complete=False,
)

SUKOON = ValueMap(
    key="sukoon",
    gender={"Male": "Male", "Female": "Female"},
    relation={"Principal": "Employee", "Spouse": "Spouse", "Child": "Child"},
    marital_status={"Single": "Single", "Married": "Married"},
    complete=False,
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
