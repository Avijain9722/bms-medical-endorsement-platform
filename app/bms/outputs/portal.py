"""Generic portal workbook generation.

One code path for every insurer. What differs per template is data: a
`TemplateBinding` says which member value fills which column, and a `ValueMap`
says how each controlled value is spelled.

The result goes to the registry's surgical writer, which regenerates from a fresh
master and blocks release on any structural change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..models import Member
from ..registry.generate import generate
from ..templates.specs import SPECS_BY_KEY, TemplateSpec
from .bindings import TemplateBinding, for_template as binding_for
from .mapping import for_template as value_map_for


class UnsupportedTemplate(Exception):
    pass


@dataclass
class BuiltRows:
    spec: TemplateSpec
    binding: TemplateBinding
    rows: list[dict]
    skipped: list[tuple[str, str]] = field(default_factory=list)
    # Columns left blank on purpose, and why. Surfaced to the user so an empty
    # cell is visibly a decision rather than an omission.
    blank_columns: dict[str, str] = field(default_factory=dict)


def resolve(
    binding: TemplateBinding,
    member: Member,
    *,
    value_map,
    index: int,
    attachments: dict | None = None,
) -> dict:
    """Turn one member into one template row."""
    attachments = attachments or {}
    row: dict[str, object] = {}

    for column, rule in binding.fields.items():
        if rule.kind == "attr":
            value = getattr(member, rule.source, None)
        elif rule.kind == "date":
            value = value_map.render_date(getattr(member, rule.source, None))
        elif rule.kind in ("gender", "relation", "marital"):
            table = {"gender": "gender", "relation": "relation", "marital": "marital_status"}[rule.kind]
            value = value_map.render(table, getattr(member, rule.source, None))
        elif rule.kind == "const":
            value = rule.constant
        elif rule.kind == "serial":
            value = index + 1
        elif rule.kind == "photo":
            value = attachments.get("photo")
        elif rule.kind == "declaration":
            value = attachments.get("declaration")
        elif rule.kind == "derive":
            value = rule.derive(member)
        else:  # pragma: no cover - a new kind must be handled explicitly
            raise ValueError(f"unknown binding kind {rule.kind!r} for column {column!r}")

        if value not in (None, ""):
            row[column] = value

    return row


def build_rows(
    template_key: str,
    members: Iterable[Member],
    *,
    attachments: dict[str, dict] | None = None,
) -> BuiltRows:
    """Turn approved members into rows for one template.

    Only approved members of the template's own transaction type are included;
    anything else is reported in `skipped` rather than silently dropped.
    """
    if template_key not in SPECS_BY_KEY:
        raise UnsupportedTemplate(f"{template_key} is not a registered template")

    spec = SPECS_BY_KEY[template_key]
    binding = binding_for(template_key)
    value_map = value_map_for(template_key)

    rows: list[dict] = []
    skipped: list[tuple[str, str]] = []

    for member in members:
        if not member.approved:
            skipped.append((member.id, "member is not approved"))
            continue
        if member.transaction_type != binding.transaction:
            skipped.append(
                (member.id, f"member is a {member.transaction_type}, not a {binding.transaction}")
            )
            continue
        rows.append(
            resolve(
                binding,
                member,
                value_map=value_map,
                index=len(rows),
                attachments=(attachments or {}).get(member.id),
            )
        )

    # Every mapped column must exist on the template. A typo here would otherwise
    # surface as a silently missing value in a submitted file.
    unknown = set(binding.fields) - set(spec.columns)
    if unknown:
        raise UnsupportedTemplate(
            f"{template_key} binding refers to columns the template does not have: "
            + ", ".join(sorted(unknown))
        )

    return BuiltRows(
        spec=spec,
        binding=binding,
        rows=rows,
        skipped=skipped,
        blank_columns=dict(binding.intentionally_blank),
    )


def generate_workbook(
    template_key: str,
    members: Iterable[Member],
    *,
    repo_root: str | Path,
    output: str | Path,
    attachments: dict[str, dict] | None = None,
) -> BuiltRows:
    """Build the rows and write the portal workbook.

    Raises `StructuralDrift` from the registry if the produced file differs
    structurally from its master, in which case nothing is released.
    """
    built = build_rows(template_key, members, attachments=attachments)
    generate(built.spec, built.rows, repo_root=repo_root, output=output)
    return built
