"""Content-addressed file storage on disk.

Uploads are written to the filesystem, not held in the browser or in a session.
A case reopened tomorrow finds its documents exactly where it left them.

Files are addressed by SHA-256, so the same document uploaded twice -- which
happens routinely, one email in the discovery set carried a byte-identical
attachment twice -- is stored once and detected as a duplicate.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .config import Settings, settings


class Storage:
    def __init__(self, config: Settings | None = None):
        self.config = config or settings
        self.root = self.config.storage_root

    def _path_for(self, digest: str) -> Path:
        # Two levels of fan-out keeps directory sizes sane on any filesystem.
        return self.root / digest[:2] / digest[2:4] / digest

    def put_bytes(self, data: bytes) -> tuple[str, bool]:
        """Store bytes. Returns (sha256, was_new)."""
        digest = hashlib.sha256(data).hexdigest()
        path = self._path_for(digest)
        if path.exists():
            return digest, False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return digest, True

    def get_bytes(self, digest: str) -> bytes:
        return self._path_for(digest).read_bytes()

    def exists(self, digest: str) -> bool:
        return self._path_for(digest).exists()

    def path(self, digest: str) -> Path:
        return self._path_for(digest)

    def delete(self, digest: str) -> bool:
        """Remove a stored blob. Used by the retention purge."""
        path = self._path_for(digest)
        if not path.exists():
            return False
        path.unlink()
        for parent in (path.parent, path.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break
        return True


class ExportStorage:
    """Generated workbooks and ZIPs, kept per case so they are easy to find."""

    def __init__(self, config: Settings | None = None):
        self.config = config or settings
        self.root = self.config.export_root

    def write(self, case_reference: str, filename: str, data: bytes) -> tuple[str, str]:
        """Write an export. Returns (relative path, sha256)."""
        directory = self.root / case_reference
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        path.write_bytes(data)
        relative = str(path.relative_to(self.root))
        return relative, hashlib.sha256(data).hexdigest()

    def absolute(self, relative_path: str) -> Path:
        return self.root / relative_path

    def delete_case(self, case_reference: str) -> int:
        directory = self.root / case_reference
        if not directory.exists():
            return 0
        count = sum(1 for _ in directory.rglob("*") if _.is_file())
        shutil.rmtree(directory)
        return count
