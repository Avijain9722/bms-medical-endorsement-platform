"""Safe archive expansion.

Clients send ZIPs, sometimes several per request and sometimes nested. The
discovery set had one ZIP per staff ID, each containing loosely-named scans.

Expansion is defensive: archive members are never written to disk by their own
name, so a crafted path such as `../../etc/passwd` cannot escape. Member counts
and total uncompressed size are capped so a zip bomb cannot exhaust the host.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..config import Settings, settings

ARCHIVE_SUFFIXES = {".zip"}
MAX_NESTING = 3


@dataclass
class ExtractedMember:
    name: str
    archive_path: str
    data: bytes


@dataclass
class ExtractionResult:
    members: list[ExtractedMember] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    password_protected: bool = False
    truncated: bool = False

    def add_skip(self, name: str, reason: str) -> None:
        self.skipped.append((name, reason))


def is_archive(filename: str) -> bool:
    return PurePosixPath(filename.lower()).suffix in ARCHIVE_SUFFIXES


def _is_safe_member(name: str) -> bool:
    """Reject absolute paths and any traversal outside the archive root."""
    if name.startswith(("/", "\\")):
        return False
    path = PurePosixPath(name.replace("\\", "/"))
    return ".." not in path.parts


def extract(
    data: bytes,
    *,
    prefix: str = "",
    config: Settings | None = None,
    _depth: int = 0,
) -> ExtractionResult:
    """Expand a ZIP into its file members, recursing into nested ZIPs."""
    config = config or settings
    result = ExtractionResult()

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        result.add_skip(prefix or "<archive>", "not a readable ZIP archive")
        return result

    total_bytes = 0
    for info in archive.infolist():
        if len(result.members) >= config.max_archive_members:
            result.truncated = True
            break
        if info.is_dir():
            continue
        if not _is_safe_member(info.filename):
            result.add_skip(info.filename, "unsafe path in archive")
            continue
        if info.flag_bits & 0x1:
            result.password_protected = True
            result.add_skip(info.filename, "password protected")
            continue

        total_bytes += info.file_size
        if total_bytes > config.max_archive_bytes:
            result.truncated = True
            result.add_skip(info.filename, "archive exceeds the configured size limit")
            break

        try:
            payload = archive.read(info)
        except (RuntimeError, zipfile.BadZipFile) as error:
            # RuntimeError is what zipfile raises for an encrypted member.
            if "password" in str(error).lower():
                result.password_protected = True
                result.add_skip(info.filename, "password protected")
            else:
                result.add_skip(info.filename, f"unreadable: {error}")
            continue

        archive_path = f"{prefix}/{info.filename}" if prefix else info.filename

        if is_archive(info.filename):
            if _depth >= MAX_NESTING:
                result.add_skip(archive_path, "nested archives too deep")
                continue
            nested = extract(payload, prefix=archive_path, config=config, _depth=_depth + 1)
            result.members.extend(nested.members)
            result.skipped.extend(nested.skipped)
            result.password_protected |= nested.password_protected
            result.truncated |= nested.truncated
            continue

        result.members.append(
            ExtractedMember(
                name=PurePosixPath(info.filename).name,
                archive_path=archive_path,
                data=payload,
            )
        )

    return result
