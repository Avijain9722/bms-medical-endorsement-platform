"""Deterministic field extraction from document text.

Everything here is rule-based: regular expressions, MRZ check digits, format
validation. No model, no external service, no guessing. A value is only produced
when the text actually contains it, and each carries a confidence derived from
how strongly it was verified -- an MRZ line whose check digits all pass is worth
more than a loose regex hit near a label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

# --------------------------------------------------------------------- types


@dataclass(frozen=True)
class Extraction:
    field_key: str
    raw_value: str
    normalised_value: str
    confidence: float
    method: str


# ------------------------------------------------------------------ patterns

# Emirates ID: 784-YYYY-NNNNNNN-C, often typed without separators.
EID_RE = re.compile(r"\b784[-\s]?(\d{4})[-\s]?(\d{7})[-\s]?(\d)\b")

# UID / unified number, always label-led -- a bare 8-10 digit run is far too
# common in these documents to claim without context.
UID_RE = re.compile(
    r"(?:U\.?I\.?D\.?(?:\s*(?:No|Number))?|Unified\s*(?:No|Number))\s*[:.\-]?\s*(\d{7,12})",
    re.IGNORECASE,
)

VISA_FILE_RE = re.compile(
    r"(?:Visa\s*File\s*(?:No|Number)|File\s*(?:No|Number))\s*[:.\-]?\s*([\d]{2,3}/\d{4}/\d{4,9})",
    re.IGNORECASE,
)

PASSPORT_LABEL_RE = re.compile(
    r"(?:Passport\s*(?:No|Number)|Document\s*No)\s*[:.\-]?\s*([A-Z0-9]{6,12})",
    re.IGNORECASE,
)

STAFF_ID_RE = re.compile(
    r"(?:Staff\s*ID|Employee\s*(?:ID|No|Number|Code)|SAP\s*ID|Emp\s*No)\s*[:.\-]?\s*([A-Z]?\d{3,10})",
    re.IGNORECASE,
)

BIRTH_CERT_RE = re.compile(
    r"(?:Birth\s*Certificate\s*(?:No|Number)?|Certificate\s*No)\s*[:.\-]?\s*([A-Z0-9/\-]{4,24})",
    re.IGNORECASE,
)

CARD_NO_RE = re.compile(
    r"(?:Card\s*(?:No|Number)|Member\s*(?:Card|No))\s*[:.\-]?\s*([A-Z0-9\-]{4,32})",
    re.IGNORECASE,
)

DATE_PATTERNS = (
    (re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b"), "ymd"),
    (
        re.compile(
            r"\b(\d{1,2})[\s\-]([A-Za-z]{3,9})[\s\-](\d{4})\b",
        ),
        "dmony",
    ),
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

DOB_LABEL_RE = re.compile(r"(?:Date\s*of\s*Birth|D\.?O\.?B\.?|Birth\s*Date)", re.IGNORECASE)
EXPIRY_LABEL_RE = re.compile(r"(?:Date\s*of\s*Expiry|Expiry|Expires|Valid\s*Until)", re.IGNORECASE)


# --------------------------------------------------------------------- dates


def normalise_date(day: int, month: int, year: int) -> str | None:
    """Return an ISO date string, or None if the components are not a real date."""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def find_dates(text: str) -> list[tuple[str, str]]:
    """All parseable dates in the text, as (raw, ISO)."""
    found: list[tuple[str, str]] = []
    for pattern, order in DATE_PATTERNS:
        for match in pattern.finditer(text):
            if order == "dmy":
                day, month, year = (int(match.group(i)) for i in (1, 2, 3))
            elif order == "ymd":
                year, month, day = (int(match.group(i)) for i in (1, 2, 3))
            else:
                day = int(match.group(1))
                month = MONTHS.get(match.group(2)[:3].lower(), 0)
                year = int(match.group(3))
                if not month:
                    continue
            iso = normalise_date(day, month, year)
            if iso:
                found.append((match.group(0), iso))
    return found


def date_near_label(text: str, label: re.Pattern[str], window: int = 60) -> tuple[str, str] | None:
    """The first date appearing shortly after a label match."""
    for label_match in label.finditer(text):
        segment = text[label_match.end() : label_match.end() + window]
        dates = find_dates(segment)
        if dates:
            return dates[0]
    return None


# ----------------------------------------------------------------------- MRZ

MRZ_LINE_RE = re.compile(r"^[A-Z0-9<]{40,46}$")
MRZ_WEIGHTS = (7, 3, 1)


def mrz_check_digit(value: str) -> int:
    total = 0
    for index, char in enumerate(value):
        if char.isdigit():
            digit = int(char)
        elif char == "<":
            digit = 0
        else:
            digit = ord(char) - 55  # 'A' -> 10
        total += digit * MRZ_WEIGHTS[index % 3]
    return total % 10


def _mrz_date(value: str, *, future_window: bool) -> str | None:
    """Expand a YYMMDD MRZ date.

    Passports carry no century. Expiry dates are in the future or recent past;
    dates of birth are in the past. That split resolves the ambiguity without
    guessing.
    """
    if len(value) != 6 or not value.isdigit():
        return None
    year, month, day = int(value[:2]), int(value[2:4]), int(value[4:6])
    current = datetime.now().year % 100
    if future_window:
        century = 2000 if year <= current + 20 else 1900
    else:
        century = 2000 if year <= current else 1900
    return normalise_date(day, month, century + year)


@dataclass(frozen=True)
class MrzData:
    surname: str
    given_names: str
    passport_no: str
    nationality: str
    date_of_birth: str | None
    sex: str | None
    expiry_date: str | None
    checks_passed: int
    checks_total: int

    @property
    def confidence(self) -> float:
        """Confidence from how many check digits validated.

        A fully-validating MRZ is the strongest evidence any of these documents
        offer, so it tops out just below certain.
        """
        if not self.checks_total:
            return 0.0
        ratio = self.checks_passed / self.checks_total
        return round(0.5 + 0.49 * ratio, 3)


def parse_mrz(text: str) -> MrzData | None:
    """Parse a TD3 (passport) machine-readable zone.

    TD3 is two 44-character lines. OCR frequently mangles the padding, so lines
    are normalised and length-checked rather than required to be exact.
    """
    candidates = [
        re.sub(r"\s+", "", line).upper()
        for line in text.splitlines()
        if MRZ_LINE_RE.match(re.sub(r"\s+", "", line).upper())
    ]
    for first, second in zip(candidates, candidates[1:]):
        if not first.startswith("P"):
            continue
        line1 = first.ljust(44, "<")[:44]
        line2 = second.ljust(44, "<")[:44]

        names = line1[5:44].split("<<", 1)
        surname = names[0].replace("<", " ").strip()
        given = (names[1].replace("<", " ").strip() if len(names) > 1 else "")

        passport_no = line2[0:9].replace("<", "").strip()
        nationality = line2[10:13].replace("<", "").strip()
        dob_raw = line2[13:19]
        sex_raw = line2[20:21]
        expiry_raw = line2[21:27]

        checks = [
            (line2[0:9], line2[9:10]),
            (dob_raw, line2[19:20]),
            (expiry_raw, line2[27:28]),
        ]
        passed = sum(
            1
            for value, expected in checks
            if expected.isdigit() and mrz_check_digit(value) == int(expected)
        )

        return MrzData(
            surname=surname,
            given_names=given,
            passport_no=passport_no,
            nationality=nationality,
            date_of_birth=_mrz_date(dob_raw, future_window=False),
            sex={"M": "Male", "F": "Female"}.get(sex_raw),
            expiry_date=_mrz_date(expiry_raw, future_window=True),
            checks_passed=passed,
            checks_total=len(checks),
        )
    return None


# ------------------------------------------------------------- Emirates ID


def normalise_emirates_id(value: str) -> str | None:
    """Return the canonical 784-YYYY-NNNNNNN-C form, or None if implausible.

    Structure is validated, not the check digit: BMS has not supplied the
    approved check-digit rule, and inventing one could reject valid cards.
    """
    digits = re.sub(r"\D", "", value)
    if len(digits) != 15 or not digits.startswith("784"):
        return None
    year = int(digits[3:7])
    if not 1900 <= year <= date.today().year:
        return None
    return f"{digits[0:3]}-{digits[3:7]}-{digits[7:14]}-{digits[14]}"


# ------------------------------------------------------------------ the pass


def extract_fields(text: str) -> list[Extraction]:
    """Everything the platform can defensibly read out of one document."""
    if not text or not text.strip():
        return []

    results: list[Extraction] = []
    seen: set[str] = set()

    def add(field_key: str, raw: str, normalised: str | None, confidence: float, method: str):
        if not normalised or field_key in seen:
            return
        seen.add(field_key)
        results.append(
            Extraction(
                field_key=field_key,
                raw_value=raw.strip()[:512],
                normalised_value=normalised[:512],
                confidence=round(confidence, 3),
                method=method,
            )
        )

    # MRZ first: where it exists it is the most reliable source on the page.
    mrz = parse_mrz(text)
    if mrz:
        confidence = mrz.confidence
        add("passport_no", mrz.passport_no, mrz.passport_no or None, confidence, "mrz")
        add("last_name", mrz.surname, mrz.surname or None, confidence, "mrz")
        add("first_name", mrz.given_names.split(" ")[0] if mrz.given_names else "",
            mrz.given_names.split(" ")[0] if mrz.given_names else None, confidence, "mrz")
        add("date_of_birth", mrz.date_of_birth or "", mrz.date_of_birth, confidence, "mrz")
        add("gender", mrz.sex or "", mrz.sex, confidence, "mrz")
        add("passport_expiry", mrz.expiry_date or "", mrz.expiry_date, confidence, "mrz")

    eid_match = EID_RE.search(text)
    if eid_match:
        normalised = normalise_emirates_id(eid_match.group(0))
        add("emirates_id", eid_match.group(0), normalised, 0.9, "regex")

    uid_match = UID_RE.search(text)
    if uid_match:
        add("unified_no", uid_match.group(0), uid_match.group(1), 0.85, "regex")

    visa_match = VISA_FILE_RE.search(text)
    if visa_match:
        add("visa_file_number", visa_match.group(0), visa_match.group(1), 0.85, "regex")

    if "passport_no" not in seen:
        passport_match = PASSPORT_LABEL_RE.search(text)
        if passport_match:
            add("passport_no", passport_match.group(0), passport_match.group(1).upper(), 0.7, "regex")

    staff_match = STAFF_ID_RE.search(text)
    if staff_match:
        add("staff_id", staff_match.group(0), staff_match.group(1), 0.7, "regex")

    birth_match = BIRTH_CERT_RE.search(text)
    if birth_match:
        add("birth_certificate_number", birth_match.group(0), birth_match.group(1), 0.7, "regex")

    card_match = CARD_NO_RE.search(text)
    if card_match:
        add("member_card_no", card_match.group(0), card_match.group(1), 0.65, "regex")

    if "date_of_birth" not in seen:
        dob = date_near_label(text, DOB_LABEL_RE)
        if dob:
            add("date_of_birth", dob[0], dob[1], 0.75, "labelled_date")

    if "passport_expiry" not in seen:
        expiry = date_near_label(text, EXPIRY_LABEL_RE)
        if expiry:
            add("passport_expiry", expiry[0], expiry[1], 0.6, "labelled_date")

    return results
