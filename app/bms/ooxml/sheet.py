"""Surgical cell writing inside a worksheet part.

Only `sheetData` and the sheet `dimension` are touched. Column definitions,
validations, conditional formatting, protection, table parts and every extension
block are left exactly as the template author wrote them.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Iterable

from .package import M, NS_MAIN

_XMLNS = re.compile(rb'xmlns:?([A-Za-z0-9_.\-]*)="([^"]+)"')
_DECL = re.compile(rb"^\s*<\?xml[^>]*\?>")
_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")


def column_index(column: str) -> int:
    """'A' -> 1, 'AA' -> 27."""
    n = 0
    for ch in column:
        n = n * 26 + (ord(ch) - 64)
    return n


def column_name(index: int) -> str:
    """1 -> 'A', 27 -> 'AA'."""
    name = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def split_ref(ref: str) -> tuple[str, int]:
    match = _CELL_REF.match(ref)
    if not match:
        raise ValueError(f"not a cell reference: {ref!r}")
    return match.group(1), int(match.group(2))


def register_namespaces(raw: bytes) -> None:
    """Re-register the document's own prefixes so serialisation preserves them.

    ElementTree invents `ns0:`-style prefixes otherwise, which would rewrite every
    element name in the part and can invalidate `mc:Ignorable` prefix references.
    """
    head = raw[:4096]
    for prefix, uri in _XMLNS.findall(head):
        ET.register_namespace(prefix.decode(), uri.decode())


def serialise(root: ET.Element, original: bytes) -> bytes:
    """Serialise an edited part, preserving its original XML declaration."""
    decl_match = _DECL.match(original)
    decl = decl_match.group(0) if decl_match else b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    body = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    return decl + body


class SharedStrings:
    """The workbook string table, with append-and-dedupe semantics."""

    def __init__(self, raw: bytes):
        self._raw = raw
        register_namespaces(raw)
        self.root = ET.fromstring(raw)
        self._values = [
            "".join(t.text or "" for t in si.iter(M + "t")) for si in self.root
        ]
        self._index = {value: i for i, value in enumerate(self._values)}
        self.dirty = False

    def intern(self, value: str) -> int:
        """Return the index for `value`, appending a new entry if needed."""
        if value in self._index:
            return self._index[value]
        si = ET.SubElement(self.root, M + "si")
        t = ET.SubElement(si, M + "t")
        t.text = value
        # Leading/trailing whitespace is significant in several portal templates
        # (for example the NAS 'Effective Date ' header), so preserve it.
        if value != value.strip():
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        index = len(self._values)
        self._values.append(value)
        self._index[value] = index
        self.dirty = True
        return index

    def bump_count(self, written: int) -> None:
        """`count` is the number of string *cells*, not unique strings."""
        self.root.set("count", str(int(self.root.get("count") or 0) + written))

    def to_bytes(self) -> bytes:
        """Serialise the table. Call after every sheet has been written."""
        self.root.set("uniqueCount", str(len(self._values)))
        return serialise(self.root, self._raw)


class SheetWriter:
    """Writes member values into a worksheet without disturbing anything else."""

    def __init__(self, raw: bytes, shared: SharedStrings):
        self._raw = raw
        register_namespaces(raw)
        self.root = ET.fromstring(raw)
        self.shared = shared
        self._sheet_data = self.root.find(M + "sheetData")
        if self._sheet_data is None:
            raise ValueError("worksheet part has no sheetData")
        self._rows = {int(r.get("r")): r for r in self._sheet_data.findall(M + "row")}
        self._string_cells_written = 0
        self.created: set[str] = set()
        """Cells this writer added. They must carry the template's prototype style."""
        self.existing_styles: dict[str, str | None] = {}
        """Style of each pre-existing cell we wrote into, captured before the write.

        Several masters pre-style their data rows individually -- ADNIC's enrolment
        form styles rows 2 to 414 differently from each other. Writing a value into
        such a cell must keep the row's own formatting, not impose row 2's.
        """

    # ------------------------------------------------------------- prototypes

    def style_prototypes(self, row_number: int) -> dict[str, str]:
        """Style index per column letter, taken from a template row.

        New cells inherit these so text columns stay text-formatted and date
        columns keep the template's date format.
        """
        row = self._rows.get(row_number)
        if row is None:
            return {}
        prototypes = {}
        for cell in row:
            style = cell.get("s")
            if style is not None:
                prototypes[split_ref(cell.get("r"))[0]] = style
        return prototypes

    def row_prototype_style(self, row_number: int) -> str | None:
        row = self._rows.get(row_number)
        return None if row is None else row.get("s")

    # ------------------------------------------------------------ row / cell

    def _row(self, row_number: int, template_row: int | None = None) -> ET.Element:
        existing = self._rows.get(row_number)
        if existing is not None:
            return existing
        row = ET.Element(M + "row", {"r": str(row_number)})
        if template_row is not None:
            source = self._rows.get(template_row)
            if source is not None:
                for attr in ("s", "customFormat", "ht", "customHeight", "spans"):
                    if source.get(attr) is not None:
                        row.set(attr, source.get(attr))
        # Keep sheetData ordered by row number; Excel requires ascending rows.
        position = 0
        for i, sibling in enumerate(self._sheet_data):
            if int(sibling.get("r")) > row_number:
                break
            position = i + 1
        self._sheet_data.insert(position, row)
        self._rows[row_number] = row
        return row

    def _cell(self, row: ET.Element, ref: str, style: str | None) -> ET.Element:
        target_index = column_index(split_ref(ref)[0])
        position = 0
        for i, cell in enumerate(row):
            index = column_index(split_ref(cell.get("r"))[0])
            if index == target_index:
                self.existing_styles.setdefault(ref, cell.get("s"))
                return cell
            if index > target_index:
                break
            position = i + 1
        cell = ET.Element(M + "c", {"r": ref})
        if style is not None:
            cell.set("s", style)
        row.insert(position, cell)
        self.created.add(ref)
        return cell

    def set_cell(self, ref: str, value, *, style: str | None = None) -> None:
        """Set one cell.

        `None` leaves the cell genuinely empty -- no value element, no type. That
        is what the log's post-submission columns require: a blank cell, never a
        placeholder, a zero or an empty string.
        """
        _, row_number = split_ref(ref)
        row = self._row(row_number)
        cell = self._cell(row, ref, style)

        for child in list(cell):
            cell.remove(child)
        cell.attrib.pop("t", None)

        if value is None or value == "":
            return

        if isinstance(value, bool):
            cell.set("t", "b")
            ET.SubElement(cell, M + "v").text = "1" if value else "0"
        elif isinstance(value, (int, float)):
            ET.SubElement(cell, M + "v").text = repr(value) if isinstance(value, float) else str(value)
        else:
            cell.set("t", "s")
            ET.SubElement(cell, M + "v").text = str(self.shared.intern(str(value)))
            self._string_cells_written += 1

    def write_row(
        self,
        row_number: int,
        values: dict[str, object],
        *,
        prototypes: dict[str, str],
        template_row: int | None = None,
    ) -> None:
        """Write a whole member row, `values` keyed by column letter."""
        self._row(row_number, template_row=template_row)
        for column, value in values.items():
            self.set_cell(f"{column}{row_number}", value, style=prototypes.get(column))

    # ---------------------------------------------------------------- closing

    def update_dimension(self) -> None:
        """Widen the cached dimension to cover what we wrote.

        Excel treats `dimension` as a hint and repairs it on open, but a stale
        value confuses other readers, so keep it honest.
        """
        dimension = self.root.find(M + "dimension")
        if dimension is None:
            return
        max_row = 1
        max_col = 1
        for row in self._sheet_data.findall(M + "row"):
            max_row = max(max_row, int(row.get("r")))
            for cell in row:
                max_col = max(max_col, column_index(split_ref(cell.get("r"))[0]))
        current = dimension.get("ref") or "A1"
        start = current.split(":")[0]
        dimension.set("ref", f"{start}:{column_name(max_col)}{max_row}")

    def to_bytes(self) -> bytes:
        self.shared.bump_count(self._string_cells_written)
        return serialise(self.root, self._raw)


def sheet_part_names(workbook_xml: bytes, rels_xml: bytes) -> dict[str, str]:
    """Map sheet name -> worksheet part, in workbook order."""
    register_namespaces(workbook_xml)
    wb = ET.fromstring(workbook_xml)
    rels = ET.fromstring(rels_xml)
    targets = {rel.get("Id"): rel.get("Target") for rel in rels}
    rel_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    parts = {}
    for sheet in wb.findall(f"{{{NS_MAIN}}}sheets/{{{NS_MAIN}}}sheet"):
        target = targets[sheet.get(rel_attr)]
        parts[sheet.get("name")] = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
    return parts


def iter_column_letters(start: str, end: str) -> Iterable[str]:
    for i in range(column_index(start), column_index(end) + 1):
        yield column_name(i)
