"""Parsing the pasted client instruction.

The production platform never reads a mailbox. A user pastes the relevant email
body into the BMS Comments field and this module reads it with deterministic
rules only.

Real client emails carry the operative detail three ways, all seen in the
discovery set: a prose sentence ("Kindly Add the employee (ID#T1225) under the
CAT D Policy"), an inline HTML table flattened to whitespace-separated columns,
and a mix of both with Arabic text interleaved. Everything parsed here is a
*suggestion* -- the user can correct every value before approval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import TransactionType

ADDITION_TERMS = (
    "addition", "add ", "adding", "enrol", "enroll", "new joiner", "new staff",
    "insurance addition", "activate",
)
DELETION_TERMS = (
    "deletion", "delete", "cancel", "cancellation", "terminate", "termination",
    "remove", "off-board", "offboard",
)

RELATION_TERMS: tuple[tuple[str, str], ...] = (
    ("principal", "Principal"),
    ("employee", "Principal"),
    ("spouse", "Spouse"),
    ("wife", "Spouse"),
    ("husband", "Spouse"),
    ("child", "Child"),
    ("son", "Child"),
    ("daughter", "Child"),
    ("new born", "Child"),
    ("newborn", "Child"),
    ("baby", "Child"),
    ("dependent", "Child"),
    ("dependant", "Child"),
    ("parent", "Parent"),
    ("mother", "Parent"),
    ("father", "Parent"),
)

MARITAL_RE = re.compile(r"\b(single|married|divorced|widowed)\b", re.IGNORECASE)
CATEGORY_RE = re.compile(r"\bCAT[\s\-]?([A-Z]\d?)\b", re.IGNORECASE)
STAFF_ID_RE = re.compile(
    r"(?:ID\s*#|ID\s*[:\-]|\bID\b\s+|Staff\s*ID\s*[:\-]?\s*|SAP\s*ID\s*[:\-]?\s*)([A-Z]{0,2}\d{3,10})",
    re.IGNORECASE,
)
BARE_ID_RE = re.compile(r"\b([A-Z]\d{4,8}|\d{4,8})\b")
CARD_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})\b")
DEPENDENT_OF_RE = re.compile(r"dependent\s*of\s*\(?([A-Z]{0,2}\d{3,10})\)?", re.IGNORECASE)
POLICY_RE = re.compile(r"\b(QIC[\w\-]*|Daman[\w\s]*Policy|NAS|ADNIC|Sukoon)\b", re.IGNORECASE)
NEWBORN_RE = re.compile(r"\bnew\s*born\b|\bnewborn\b", re.IGNORECASE)

# Boilerplate that carries no instruction and would otherwise pollute matching.
NOISE_PREFIXES = (
    "caution: this email originated",
    "disclaimer:",
    "this is an e-mail from",
    "confidential",
)


@dataclass
class MemberHint:
    """A member the instruction appears to be about."""

    staff_id: str | None = None
    name: str | None = None
    relation: str | None = None
    marital_status: str | None = None
    category: str | None = None
    principal_staff_id: str | None = None
    member_card_no: str | None = None
    source_line: str = ""


@dataclass
class ParsedInstruction:
    transaction_type: str | None = None
    transaction_confidence: float = 0.0
    category: str | None = None
    policy_hint: str | None = None
    is_newborn: bool = False
    staff_ids: list[str] = field(default_factory=list)
    card_numbers: list[str] = field(default_factory=list)
    members: list[MemberHint] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _clean(text: str) -> list[str]:
    """Drop legal boilerplate and blank lines, keep the operative content."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if any(line.lower().startswith(prefix) for prefix in NOISE_PREFIXES):
            continue
        lines.append(line)
    return lines


def detect_transaction(text: str) -> tuple[str | None, float]:
    """Addition or deletion, with a confidence based on how clear the wording is."""
    lowered = text.lower()
    additions = sum(lowered.count(term) for term in ADDITION_TERMS)
    deletions = sum(lowered.count(term) for term in DELETION_TERMS)

    if not additions and not deletions:
        return None, 0.0
    if additions and deletions:
        # Genuinely mixed language. Report the leader but keep confidence low so
        # the user is asked rather than told.
        leader = TransactionType.ADDITION if additions >= deletions else TransactionType.DELETION
        total = additions + deletions
        return leader.value, round(0.4 + 0.2 * abs(additions - deletions) / total, 3)
    if additions:
        return TransactionType.ADDITION.value, min(0.95, 0.6 + 0.1 * additions)
    return TransactionType.DELETION.value, min(0.95, 0.6 + 0.1 * deletions)


def _relation_in(line: str) -> str | None:
    lowered = line.lower()
    for term, relation in RELATION_TERMS:
        if term in lowered:
            return relation
    return None


def _looks_like_name(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z'\-\.]{1,}", token))


def _name_from_line(line: str) -> str | None:
    """Longest run of name-shaped words, ignoring known label words."""
    labels = {
        "id", "sap", "card", "number", "company", "beneficial", "name", "cat",
        "insurance", "category", "date", "marital", "martial", "status", "policy",
        "designation", "dependent", "dependant", "of", "or", "principal", "employee",
        "single", "married", "divorced", "widowed", "child", "spouse", "parent", "new",
        "born", "kindly", "please", "the", "under", "and", "add", "delete", "cancel",
    }
    best: list[str] = []
    current: list[str] = []
    for token in re.split(r"[\s,;|\\/]+", line):
        stripped = token.strip("()[]:.-")
        if _looks_like_name(stripped) and stripped.lower() not in labels:
            current.append(stripped)
        else:
            if len(current) > len(best):
                best = current
            current = []
    if len(current) > len(best):
        best = current
    if len(best) < 2:
        return None
    return " ".join(best[:4]).title()


def parse(text: str) -> ParsedInstruction:
    """Read a pasted client email body."""
    result = ParsedInstruction()
    if not text or not text.strip():
        return result

    lines = _clean(text)
    joined = "\n".join(lines)

    result.transaction_type, result.transaction_confidence = detect_transaction(joined)
    result.is_newborn = bool(NEWBORN_RE.search(joined))

    category_match = CATEGORY_RE.search(joined)
    if category_match:
        result.category = f"CAT {category_match.group(1).upper()}"

    policy_match = POLICY_RE.search(joined)
    if policy_match:
        result.policy_hint = policy_match.group(1)

    for match in STAFF_ID_RE.finditer(joined):
        value = match.group(1).upper()
        if value not in result.staff_ids:
            result.staff_ids.append(value)

    for match in CARD_RE.finditer(joined):
        if match.group(1) not in result.card_numbers:
            result.card_numbers.append(match.group(1))

    # One hint per line that carries an identifier -- the tabular emails put one
    # member per row, and the prose ones mention a single member.
    for line in lines:
        ids = [m.group(1).upper() for m in STAFF_ID_RE.finditer(line)]
        if not ids:
            ids = [m.group(1).upper() for m in BARE_ID_RE.finditer(line)]
        cards = [m.group(1) for m in CARD_RE.finditer(line)]
        relation = _relation_in(line)
        marital = MARITAL_RE.search(line)
        category = CATEGORY_RE.search(line)
        dependent_of = DEPENDENT_OF_RE.search(line)
        name = _name_from_line(line)

        # A member is only asserted when the line carries an identifier. A name
        # on its own cannot be told apart from a greeting or a signature block --
        # "Dear Team" and "Best regards" are both two capitalised words -- and
        # inventing a member from one would be worse than missing it, since the
        # reviewer can always add a member by hand.
        if not ids and not cards:
            continue
        # A line with only a stray number is noise, not a member.
        if not name and not cards and len(ids) == 1 and len(line.split()) <= 2:
            continue

        principal = dependent_of.group(1).upper() if dependent_of else None
        staff_id = None
        for candidate in ids:
            if candidate != principal:
                staff_id = candidate
                break
        if staff_id is None and ids:
            staff_id = ids[0]

        result.members.append(
            MemberHint(
                staff_id=staff_id,
                name=name,
                relation=relation,
                marital_status=marital.group(1).title() if marital else None,
                category=f"CAT {category.group(1).upper()}" if category else result.category,
                principal_staff_id=principal,
                member_card_no=cards[0] if cards else None,
                source_line=line[:300],
            )
        )

    if result.transaction_type is None:
        result.notes.append(
            "No addition or deletion wording found in the pasted instruction; "
            "the transaction type must be selected manually."
        )
    if not result.members:
        result.notes.append(
            "No member could be identified from the pasted instruction; "
            "members must be added from the uploaded documents or by hand."
        )
    return result
