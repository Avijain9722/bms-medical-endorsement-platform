"""The client master library.

BMS asked for a centralised store of clients, sub-groups, legal entities and
insurer policies that every user shares, maintained either by hand or by
importing an Excel file.

Reading the import workbook reuses the project's own OOXML reader rather than
adding a spreadsheet library to the runtime: the same reasoning as the template
writer, and it keeps `requirements.txt` at five packages.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .models import Client, ClientPolicy, LegalEntity, SubGroup
from .ooxml.package import M, OoxmlPackage
from .ooxml.sheet import sheet_part_names, split_ref

# Accepted spellings for each import column. Clients and BMS staff label these
# inconsistently, so match generously on the header rather than rejecting a file.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "client": ("client", "client name", "company", "company name", "group", "group name"),
    "code": ("code", "client code"),
    "sub_group": ("sub-group", "sub group", "subgroup", "sub-group name", "sub group name"),
    "legal_entity": ("legal entity", "entity", "entity name", "legal entity name"),
    "contract_name": ("contract name", "contract", "policy name"),
    "insurer": ("insurer", "insurer/tpa", "insurer / tpa", "tpa"),
    "network": ("network",),
    "policy_no": ("policy no", "policy no.", "policy number", "policy"),
    "category": ("category", "cat", "plan/category", "plan"),
    "emirate": ("emirate", "emirates", "region"),
    "addition_template_key": ("addition template", "addition template key", "addition"),
    "deletion_template_key": ("deletion template", "deletion template key", "deletion"),
}

VALID_EMIRATES = {
    "abu dhabi": "Abu Dhabi",
    "auh": "Abu Dhabi",
    "dubai": "Dubai",
    "dxb": "Dubai",
    "northern emirates": "Northern Emirates",
    "ne": "Northern Emirates",
    "sharjah": "Northern Emirates",
}

# Client-master workbooks are small tabular inputs. These caps keep a compressed
# upload from expanding into an unbounded number of in-memory XML parts.
MAX_IMPORT_PARTS = 10_000
MAX_IMPORT_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


@dataclass
class ImportSummary:
    clients_created: int = 0
    sub_groups_created: int = 0
    legal_entities_created: int = 0
    policies_created: int = 0
    rows_read: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def total_created(self) -> int:
        return (
            self.clients_created
            + self.sub_groups_created
            + self.legal_entities_created
            + self.policies_created
        )


def normalise_emirate(value: str | None) -> str | None:
    if not value:
        return None
    return VALID_EMIRATES.get(value.strip().lower(), value.strip())


# ------------------------------------------------------------------ workbook


def _safe_xml(data: bytes) -> ET.Element:
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("workbook XML declarations and entities are not supported")
    return ET.fromstring(data)


def read_rows(data: bytes, sheet_name: str | None = None) -> list[dict[str, str]]:
    """Read the first worksheet of an .xlsx as a list of {header: value}."""
    pkg = OoxmlPackage.open_bytes(
        data,
        max_parts=MAX_IMPORT_PARTS,
        max_uncompressed_bytes=MAX_IMPORT_UNCOMPRESSED_BYTES,
    )
    workbook_xml = pkg.read("xl/workbook.xml")
    relationships_xml = pkg.read("xl/_rels/workbook.xml.rels")
    # sheet_part_names parses these parts too; validate them first so uploaded
    # XML cannot declare external entities or a document type.
    _safe_xml(workbook_xml)
    _safe_xml(relationships_xml)
    parts = sheet_part_names(workbook_xml, relationships_xml)
    if not parts:
        return []
    part = parts.get(sheet_name) if sheet_name else next(iter(parts.values()))
    if part is None:
        raise KeyError(f"workbook has no sheet named {sheet_name!r}")

    shared: list[str] = []
    if "xl/sharedStrings.xml" in pkg:
        shared = [
            "".join(t.text or "" for t in si.iter(M + "t"))
            for si in _safe_xml(pkg.read("xl/sharedStrings.xml"))
        ]

    def value_of(cell: ET.Element) -> str:
        inline = cell.find(M + "is")
        if inline is not None:
            return "".join(t.text or "" for t in inline.iter(M + "t"))
        node = cell.find(M + "v")
        if node is None or node.text is None:
            return ""
        if cell.get("t") == "s":
            try:
                return shared[int(node.text)]
            except (ValueError, IndexError):
                return node.text
        return node.text

    grid: dict[int, dict[str, str]] = {}
    root = _safe_xml(pkg.read(part))
    for row in root.findall(f"{M}sheetData/{M}row"):
        index = int(row.get("r"))
        cells = {}
        for cell in row:
            column, _ = split_ref(cell.get("r"))
            text = value_of(cell).strip()
            if text:
                cells[column] = text
        if cells:
            grid[index] = cells
    if not grid:
        return []

    header_index = min(grid)
    headers = {column: text for column, text in grid[header_index].items()}

    rows = []
    for index in sorted(grid):
        if index == header_index:
            continue
        rows.append(
            {headers[column]: text for column, text in grid[index].items() if column in headers}
        )
    return rows


def map_columns(row: dict[str, str]) -> dict[str, str]:
    """Translate a spreadsheet row into canonical field names."""
    lowered = {key.strip().lower(): value for key, value in row.items()}
    mapped: dict[str, str] = {}
    for field_name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered and lowered[alias]:
                mapped[field_name] = lowered[alias].strip()
                break
    return mapped


# --------------------------------------------------------------------- upsert


def get_or_create_client(session: Session, name: str, *, code: str | None = None) -> tuple[Client, bool]:
    existing = session.scalar(select(Client).where(Client.name == name))
    if existing:
        if code and not existing.code:
            existing.code = code
        return existing, False
    client = Client(name=name, code=code)
    session.add(client)
    session.flush()
    return client, True


def get_or_create_sub_group(
    session: Session, client: Client, name: str, *, emirate: str | None = None
) -> tuple[SubGroup, bool]:
    existing = session.scalar(
        select(SubGroup).where(SubGroup.client_id == client.id, SubGroup.name == name)
    )
    if existing:
        if emirate and not existing.emirate:
            existing.emirate = normalise_emirate(emirate)
        return existing, False
    sub_group = SubGroup(client_id=client.id, name=name, emirate=normalise_emirate(emirate))
    session.add(sub_group)
    session.flush()
    return sub_group, True


def get_or_create_entity(
    session: Session, client: Client, name: str, *, contract_name: str | None = None
) -> tuple[LegalEntity, bool]:
    existing = session.scalar(
        select(LegalEntity).where(LegalEntity.client_id == client.id, LegalEntity.name == name)
    )
    if existing:
        if contract_name and not existing.contract_name:
            existing.contract_name = contract_name
        return existing, False
    entity = LegalEntity(client_id=client.id, name=name, contract_name=contract_name or name)
    session.add(entity)
    session.flush()
    return entity, True


def get_or_create_policy(
    session: Session,
    client: Client,
    *,
    insurer: str,
    entity: LegalEntity | None = None,
    **fields,
) -> tuple[ClientPolicy, bool]:
    query = select(ClientPolicy).where(
        ClientPolicy.client_id == client.id,
        ClientPolicy.insurer == insurer,
        ClientPolicy.policy_no == fields.get("policy_no"),
        ClientPolicy.category == fields.get("category"),
    )
    existing = session.scalar(query)
    if existing:
        return existing, False
    policy = ClientPolicy(
        client_id=client.id,
        legal_entity_id=entity.id if entity else None,
        insurer=insurer,
        **{key: value for key, value in fields.items() if key != "emirate"},
        emirate=normalise_emirate(fields.get("emirate")),
    )
    session.add(policy)
    session.flush()
    return policy, True


def import_workbook(session: Session, data: bytes, *, actor: str) -> ImportSummary:
    """Import a client-master workbook.

    One row per client/sub-group/policy combination. A blank cell means "not
    supplied" and is skipped -- it never overwrites an existing value with
    nothing.
    """
    summary = ImportSummary()
    for raw in read_rows(data):
        mapped = map_columns(raw)
        summary.rows_read += 1

        client_name = mapped.get("client")
        if not client_name:
            summary.skipped.append(f"row {summary.rows_read}: no client name")
            continue

        client, created = get_or_create_client(session, client_name, code=mapped.get("code"))
        summary.clients_created += created

        entity = None
        if mapped.get("legal_entity"):
            entity, created = get_or_create_entity(
                session,
                client,
                mapped["legal_entity"],
                contract_name=mapped.get("contract_name"),
            )
            summary.legal_entities_created += created

        if mapped.get("sub_group"):
            _, created = get_or_create_sub_group(
                session, client, mapped["sub_group"], emirate=mapped.get("emirate")
            )
            summary.sub_groups_created += created

        if mapped.get("insurer"):
            _, created = get_or_create_policy(
                session,
                client,
                insurer=mapped["insurer"],
                entity=entity,
                network=mapped.get("network"),
                policy_no=mapped.get("policy_no"),
                category=mapped.get("category"),
                emirate=mapped.get("emirate"),
                addition_template_key=mapped.get("addition_template_key"),
                deletion_template_key=mapped.get("deletion_template_key"),
            )
            summary.policies_created += created

    audit.record(
        session,
        action="master.import",
        entity_type="client_master",
        actor=actor,
        detail={
            "rows_read": summary.rows_read,
            "clients_created": summary.clients_created,
            "sub_groups_created": summary.sub_groups_created,
            "legal_entities_created": summary.legal_entities_created,
            "policies_created": summary.policies_created,
            "skipped": len(summary.skipped),
        },
    )
    return summary


def active_clients(session: Session) -> Iterable[Client]:
    return session.scalars(select(Client).where(Client.active.is_(True)).order_by(Client.name))
