"""Document classification.

Filenames are unreliable. The discovery ZIPs contained `PIC.jpg`, `Cancelled.pdf`
and `khidmah stamped visa.jpeg` alongside `Passport Front.jpg` -- no consistent
convention. So classification is driven by document content, with the filename
used only as a weak tie-breaker.

Anything the rules cannot place confidently is returned as UNKNOWN and put in
front of a user, rather than being forced into a category.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..models import DocumentType
from .fields import parse_mrz

# Keyword evidence per document type. Weights are deliberately coarse: these are
# signals for a human-reviewed decision, not a scoring model to tune.
KEYWORDS: dict[DocumentType, tuple[tuple[str, float], ...]] = {
    DocumentType.PASSPORT: (
        ("passport", 3.0),
        ("place of issue", 1.5),
        ("date of issue", 1.0),
        ("nationality", 0.8),
        ("holder", 0.6),
    ),
    DocumentType.EMIRATES_ID: (
        ("emirates id", 3.0),
        ("identity card", 2.5),
        ("united arab emirates", 1.0),
        ("id number", 1.5),
        ("784-", 2.0),
    ),
    DocumentType.VISA: (
        ("residence", 2.0),
        ("visa", 2.5),
        ("permit", 1.0),
        ("sponsor", 1.5),
        ("profession", 1.0),
        ("u.i.d", 1.5),
    ),
    DocumentType.ENTRY_PERMIT: (
        ("entry permit", 3.0),
        ("change status", 2.5),
        ("change of status", 2.5),
        ("entry visa", 2.0),
    ),
    DocumentType.BIRTH_CERTIFICATE: (
        ("birth certificate", 3.5),
        ("certificate of birth", 3.5),
        ("live birth", 2.0),
        ("notification of birth", 2.5),
        ("mother", 1.0),
        ("father", 1.0),
    ),
    DocumentType.INSURANCE_CARD: (
        ("insurance card", 3.0),
        ("member card", 2.0),
        ("policy no", 1.5),
        ("payer", 1.0),
        ("network", 1.0),
    ),
    DocumentType.CERTIFICATE_OF_CONTINUITY: (
        ("certificate of continuity", 4.0),
        ("continuity of cover", 3.0),
        ("continuous cover", 2.5),
    ),
    DocumentType.MEDICAL_DECLARATION: (
        ("medical application form", 3.0),
        ("declaration of health", 3.0),
        ("pre-existing", 2.0),
        ("medical declaration", 3.0),
    ),
    DocumentType.CANCELLATION: (
        ("cancel", 2.5),
        ("cancellation", 3.0),
        ("cancelled residence", 3.5),
        ("termination", 1.5),
    ),
    DocumentType.CLIENT_SHEET: (
        ("staff id", 1.5),
        ("category", 1.0),
        ("marital status", 1.0),
        ("beneficiary", 1.0),
    ),
}

FILENAME_HINTS: tuple[tuple[re.Pattern[str], DocumentType, float], ...] = (
    (re.compile(r"passport", re.I), DocumentType.PASSPORT, 1.0),
    (re.compile(r"\beid\b|emirates.?id", re.I), DocumentType.EMIRATES_ID, 1.0),
    (re.compile(r"visa", re.I), DocumentType.VISA, 1.0),
    (re.compile(r"change.?status|entry.?(permit|date)", re.I), DocumentType.ENTRY_PERMIT, 1.0),
    (re.compile(r"birth|\bbc\b", re.I), DocumentType.BIRTH_CERTIFICATE, 1.0),
    (re.compile(r"cancel", re.I), DocumentType.CANCELLATION, 1.0),
    (re.compile(r"\bcoc\b|continuity", re.I), DocumentType.CERTIFICATE_OF_CONTINUITY, 1.0),
    (re.compile(r"\bmaf\b|declaration", re.I), DocumentType.MEDICAL_DECLARATION, 1.0),
    (re.compile(r"photo|\bpic\b|picture", re.I), DocumentType.PHOTOGRAPH, 1.0),
)

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png"}

# Below this the classification is not trustworthy enough to act on.
MIN_CONFIDENCE = 0.35


@dataclass(frozen=True)
class Classification:
    document_type: DocumentType
    confidence: float
    rationale: str

    @property
    def needs_manual_review(self) -> bool:
        return self.document_type is DocumentType.UNKNOWN or self.confidence < MIN_CONFIDENCE


def classify(filename: str, text: str | None) -> Classification:
    scores: dict[DocumentType, float] = {}
    reasons: dict[DocumentType, list[str]] = {}

    lowered = (text or "").lower()

    if lowered:
        for document_type, keywords in KEYWORDS.items():
            for keyword, weight in keywords:
                if keyword in lowered:
                    scores[document_type] = scores.get(document_type, 0.0) + weight
                    reasons.setdefault(document_type, []).append(f"text:{keyword}")

        # A valid MRZ is close to conclusive for a passport.
        mrz = parse_mrz(text or "")
        if mrz and mrz.checks_passed >= 2:
            scores[DocumentType.PASSPORT] = scores.get(DocumentType.PASSPORT, 0.0) + 5.0
            reasons.setdefault(DocumentType.PASSPORT, []).append("mrz verified")

    for pattern, document_type, weight in FILENAME_HINTS:
        if pattern.search(filename):
            scores[document_type] = scores.get(document_type, 0.0) + weight
            reasons.setdefault(document_type, []).append(f"filename:{pattern.pattern}")

    # An image with no readable text is most likely the member photograph, but
    # only weakly -- it could equally be an unreadable scan, so it stays low
    # enough to land in manual review.
    if not lowered and Path(filename).suffix.lower() in PHOTO_SUFFIXES:
        scores[DocumentType.PHOTOGRAPH] = scores.get(DocumentType.PHOTOGRAPH, 0.0) + 1.0
        reasons.setdefault(DocumentType.PHOTOGRAPH, []).append("image with no text layer")

    if not scores:
        return Classification(DocumentType.UNKNOWN, 0.0, "no matching evidence")

    best = max(scores.items(), key=lambda item: item[1])
    document_type, score = best
    runner_up = sorted(scores.values(), reverse=True)
    margin = score - (runner_up[1] if len(runner_up) > 1 else 0.0)

    # Confidence rises with the winning score and with how clearly it beat the
    # next candidate, so an ambiguous document does not present as certain.
    confidence = min(0.95, (score / 8.0) * 0.7 + min(margin / 5.0, 1.0) * 0.3)

    if confidence < MIN_CONFIDENCE:
        return Classification(
            DocumentType.UNKNOWN,
            round(confidence, 3),
            f"best guess {document_type.value} but evidence too weak",
        )

    return Classification(
        document_type,
        round(confidence, 3),
        ", ".join(reasons.get(document_type, [])[:4]),
    )
