"""Read/write access to an OOXML package with byte-faithful part preservation.

The endorsement platform must populate insurer workbooks without changing anything
else about them. Every general-purpose Excel library rewrites parts it does not
understand -- macros, form controls, printer settings, sensitivity labels. This
module therefore treats a workbook as what it physically is: an ordered set of
ZIP parts. Only the parts we deliberately edit are re-serialised; every other part
is carried across as the exact bytes we read.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Namespaces used across the spreadsheet parts.
NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_X14 = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
NS_XM = "http://schemas.microsoft.com/office/excel/2006/main"

M = "{%s}" % NS_MAIN
R = "{%s}" % NS_REL
X14 = "{%s}" % NS_X14
XM = "{%s}" % NS_XM


@dataclass(frozen=True)
class PartInfo:
    """ZIP metadata we preserve when rewriting a package."""

    name: str
    compress_type: int
    date_time: tuple
    create_system: int
    external_attr: int


class OoxmlPackage:
    """An OOXML package held in memory as ordered parts.

    Parts are keyed by their archive name and kept in the original order so a
    rewritten package presents its parts to Excel in the sequence Excel wrote them.
    """

    def __init__(self, parts: dict[str, bytes], order: list[str], infos: dict[str, PartInfo]):
        self._parts = parts
        self._order = order
        self._infos = infos
        self._dirty: set[str] = set()

    # ---------------------------------------------------------------- loading

    @classmethod
    def open_bytes(cls, data: bytes) -> "OoxmlPackage":
        """Open a package already held in memory, such as an upload."""
        import io

        return cls._read(io.BytesIO(data))

    @classmethod
    def open(cls, path: str | Path) -> "OoxmlPackage":
        return cls._read(path)

    @classmethod
    def _read(cls, source) -> "OoxmlPackage":
        parts: dict[str, bytes] = {}
        order: list[str] = []
        infos: dict[str, PartInfo] = {}
        with zipfile.ZipFile(source) as zf:
            for info in zf.infolist():
                parts[info.filename] = zf.read(info.filename)
                order.append(info.filename)
                infos[info.filename] = PartInfo(
                    name=info.filename,
                    compress_type=info.compress_type,
                    date_time=info.date_time,
                    create_system=info.create_system,
                    external_attr=info.external_attr,
                )
        return cls(parts, order, infos)

    # ------------------------------------------------------------ part access

    def __contains__(self, name: str) -> bool:
        return name in self._parts

    def read(self, name: str) -> bytes:
        return self._parts[name]

    def write(self, name: str, data: bytes) -> None:
        """Replace a part's bytes. The part must already exist.

        Adding or removing parts would change the package structure, which is
        exactly what the platform must never do to a registered template.
        """
        if name not in self._parts:
            raise KeyError(
                f"refusing to add part {name!r}: templates are structurally immutable"
            )
        self._parts[name] = data
        self._dirty.add(name)

    @property
    def names(self) -> list[str]:
        return list(self._order)

    @property
    def dirty(self) -> frozenset[str]:
        """Parts modified since the package was opened."""
        return frozenset(self._dirty)

    def part_sha256(self, name: str) -> str:
        return hashlib.sha256(self._parts[name]).hexdigest()

    # ----------------------------------------------------------------- saving

    def save(self, path: str | Path) -> None:
        """Write the package out, preserving part order and ZIP metadata."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in self._order:
                info = self._infos[name]
                zi = zipfile.ZipInfo(filename=name, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zi.create_system = info.create_system
                zi.external_attr = info.external_attr
                zf.writestr(zi, self._parts[name])
        shutil.move(str(tmp), str(path))


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
