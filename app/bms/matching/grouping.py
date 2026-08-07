"""Member identification and document grouping.

The task: given a pasted instruction and a pile of documents, work out how many
people the request is about and which documents belong to each.

Filenames alone cannot do this -- the discovery ZIPs used `PIC.jpg` and
`Passport Front.jpg` with no member name anywhere in the name. So matching runs
on identifiers read out of the documents, with the archive a document arrived in
as a strong secondary signal: clients routinely send one ZIP per staff ID.

Every grouping decision carries a score and a reason, and every one of them can
be overridden by the reviewer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Identifiers unique to one person, strong enough to bind a document alone.
STRONG_KEYS = ("passport_no", "emirates_id", "unified_no", "visa_file_number", "member_card_no")

# Staff ID sits between the two. The NAS field guide calls it "the main internal
# tracking reference", and clients name their ZIPs after it -- but some clients
# put the *principal's* staff ID on a dependant's row, so it is not proof of
# identity on its own. It scores just over the assignment threshold: enough to
# group a document when nothing contradicts it, and any resulting mis-grouping
# surfaces in review, where documents can be reassigned.
MEDIUM_KEYS = ("staff_id",)

# Supporting evidence: meaningful in combination, not alone.
WEAK_KEYS = ("date_of_birth", "birth_certificate_number")

STRONG_SCORE = 6.0
MEDIUM_SCORE = 4.0
WEAK_SCORE = 2.0
ARCHIVE_SCORE = 3.0
# Both a first and a last name matching reaches the assignment threshold; a
# single shared token deliberately does not, since first names repeat within a
# family and across a batch.
NAME_SCORE = 3.0

# Below this, a document is left unassigned for the reviewer rather than being
# attached to a member on thin evidence.
ASSIGN_THRESHOLD = 3.0


def normalise_identifier(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return cleaned or None


def normalise_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z ]", "", value.lower()).strip()


def name_tokens(value: str | None) -> set[str]:
    return {token for token in normalise_name(value).split() if len(token) > 2}


@dataclass
class DocumentIdentity:
    """What one document says about who it belongs to."""

    file_id: str
    filename: str
    archive_root: str | None = None
    identifiers: dict[str, str] = field(default_factory=dict)
    names: set[str] = field(default_factory=set)
    confidence: float = 0.0

    def identifier(self, key: str) -> str | None:
        return normalise_identifier(self.identifiers.get(key))


@dataclass
class MemberGroup:
    """A person, and the documents believed to be theirs."""

    key: str
    identifiers: dict[str, str] = field(default_factory=dict)
    names: set[str] = field(default_factory=set)
    archive_roots: set[str] = field(default_factory=set)
    file_ids: list[str] = field(default_factory=list)
    staff_id: str | None = None
    display_name: str | None = None
    relation: str | None = None
    marital_status: str | None = None
    category: str | None = None
    principal_staff_id: str | None = None
    member_card_no: str | None = None
    origin: str = "instruction"
    reasons: list[str] = field(default_factory=list)

    def identifier(self, key: str) -> str | None:
        return normalise_identifier(self.identifiers.get(key))

    def absorb(self, identity: DocumentIdentity, reason: str) -> None:
        self.file_ids.append(identity.file_id)
        for key, value in identity.identifiers.items():
            self.identifiers.setdefault(key, value)
        self.names |= identity.names
        if identity.archive_root:
            self.archive_roots.add(identity.archive_root)
        self.reasons.append(f"{identity.filename}: {reason}")


def archive_root(archive_path: str | None) -> str | None:
    """The top-level archive a document came from, used as a grouping signal."""
    if not archive_path:
        return None
    return archive_path.split("/", 1)[0] or None


def score(group: MemberGroup, identity: DocumentIdentity) -> tuple[float, list[str]]:
    """How strongly a document belongs to a member, and why."""
    total = 0.0
    reasons: list[str] = []

    for key in STRONG_KEYS:
        left, right = group.identifier(key), identity.identifier(key)
        if left and right and left == right:
            total += STRONG_SCORE
            reasons.append(f"{key} matches")

    for key in MEDIUM_KEYS:
        left, right = group.identifier(key), identity.identifier(key)
        if left and right and left == right:
            total += MEDIUM_SCORE
            reasons.append(f"{key} matches")

    for key in WEAK_KEYS:
        left, right = group.identifier(key), identity.identifier(key)
        if left and right and left == right:
            total += WEAK_SCORE
            reasons.append(f"{key} matches")

    if identity.archive_root and identity.archive_root in group.archive_roots:
        total += ARCHIVE_SCORE
        reasons.append(f"same archive ({identity.archive_root})")

    # A staff ID appearing in the archive name is the convention clients use when
    # they send one ZIP per employee.
    if identity.archive_root and group.staff_id:
        if normalise_identifier(group.staff_id) in normalise_identifier(identity.archive_root or ""):
            total += ARCHIVE_SCORE
            reasons.append(f"staff id in archive name ({identity.archive_root})")

    group_tokens = set()
    for name in group.names | ({group.display_name} if group.display_name else set()):
        group_tokens |= name_tokens(name)
    identity_tokens: set[str] = set()
    for name in identity.names:
        identity_tokens |= name_tokens(name)
    shared = group_tokens & identity_tokens
    if shared:
        total += NAME_SCORE * min(len(shared), 2) / 2
        reasons.append(f"name overlap ({', '.join(sorted(shared))})")

    return total, reasons


def group_documents(
    groups: list[MemberGroup],
    identities: list[DocumentIdentity],
) -> tuple[list[MemberGroup], list[DocumentIdentity]]:
    """Assign documents to members, creating members where a document warrants it.

    Returns the groups and the documents that could not be placed confidently.
    """
    working = list(groups)
    unassigned: list[DocumentIdentity] = []

    # Strongest evidence first, so a passport establishes the member and weaker
    # documents attach to it rather than spawning duplicates.
    ordered = sorted(
        identities,
        key=lambda identity: (
            -sum(1 for key in STRONG_KEYS if identity.identifier(key)),
            -identity.confidence,
        ),
    )

    for identity in ordered:
        best_group: MemberGroup | None = None
        best_score = 0.0
        best_reasons: list[str] = []

        for group in working:
            value, reasons = score(group, identity)
            if value > best_score:
                best_group, best_score, best_reasons = group, value, reasons

        if best_group is not None and best_score >= ASSIGN_THRESHOLD:
            best_group.absorb(identity, "; ".join(best_reasons))
            continue

        # No home for it. If the document identifies a person on its own, that is
        # a member the instruction did not mention -- which happens, and must be
        # surfaced rather than dropped.
        if any(identity.identifier(key) for key in STRONG_KEYS):
            new_group = MemberGroup(
                key=f"doc:{identity.file_id}",
                origin="document",
            )
            new_group.absorb(identity, "new member identified from document")
            working.append(new_group)
        else:
            unassigned.append(identity)

    return working, unassigned


def link_principals(groups: list[MemberGroup]) -> list[tuple[MemberGroup, MemberGroup | None, str]]:
    """Resolve each dependant to its principal.

    Ordered by reliability: an explicit principal card number, then the staff ID
    the instruction named as the principal, then the employee ID itself. Anything
    unresolved is returned with a reason so the caller can raise a blocking flag.
    """
    by_staff_id = {
        normalise_identifier(group.staff_id): group for group in groups if group.staff_id
    }
    by_card = {
        normalise_identifier(group.member_card_no): group
        for group in groups
        if group.member_card_no
    }

    resolved: list[tuple[MemberGroup, MemberGroup | None, str]] = []
    for group in groups:
        if (group.relation or "").lower() in ("", "principal"):
            continue

        if group.member_card_no:
            candidate = by_card.get(normalise_identifier(group.member_card_no))
            if candidate and candidate is not group:
                resolved.append((group, candidate, "matched on principal card number"))
                continue

        if group.principal_staff_id:
            candidate = by_staff_id.get(normalise_identifier(group.principal_staff_id))
            if candidate and candidate is not group:
                resolved.append((group, candidate, "matched on principal staff id"))
                continue
            resolved.append(
                (
                    group,
                    None,
                    f"principal staff id {group.principal_staff_id} is not in this case",
                )
            )
            continue

        resolved.append((group, None, "no principal identified in the instruction"))

    return resolved
