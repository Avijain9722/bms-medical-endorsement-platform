"""Local virus scanning for uploads.

Client attachments arrive from outside the organisation and are opened by BMS
staff, so they are scanned before anything else touches them.

ClamAV is used because it runs locally: no file leaves the host, which the same
rule that governs OCR requires. `clamdscan` is preferred when the daemon is
running, since it avoids reloading the signature database per file.

When no scanner is installed the verdict is `unavailable`, not `clean`. An
unscanned file is recorded as unscanned and raised for the user, because
reporting a file as safe without having checked would be worse than saying
nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

CLEAN = "clean"
INFECTED = "infected"
UNAVAILABLE = "unavailable"
ERROR = "error"


@dataclass(frozen=True)
class ScanResult:
    verdict: str
    engine: str
    detail: str = ""

    @property
    def blocks_upload(self) -> bool:
        return self.verdict == INFECTED

    @property
    def needs_notice(self) -> bool:
        return self.verdict in (UNAVAILABLE, ERROR)


def _scanner() -> tuple[str, list[str]] | None:
    """The available scanner command, daemon first."""
    for command in ("clamdscan", "clamscan"):
        path = shutil.which(command)
        if path:
            # --no-summary keeps the output to one line per file.
            return command, [path, "--no-summary", "--stdout"]
    return None


def available() -> bool:
    return _scanner() is not None


def scan_bytes(data: bytes, *, filename: str = "upload") -> ScanResult:
    """Scan a payload before it is stored."""
    scanner = _scanner()
    if scanner is None:
        return ScanResult(
            UNAVAILABLE,
            "none",
            "No local virus scanner is installed. Install ClamAV to enable scanning; "
            "uploads are recorded as unscanned until then.",
        )

    engine, command = scanner
    suffix = Path(filename).suffix[:12]
    with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
        handle.write(data)
        handle.flush()
        try:
            completed = subprocess.run(
                [*command, handle.name], capture_output=True, timeout=120, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return ScanResult(ERROR, engine, f"scan could not be completed: {error}")

    output = completed.stdout.decode("utf-8", "replace").strip()
    # ClamAV: 0 = clean, 1 = infected, 2 = error.
    if completed.returncode == 0:
        return ScanResult(CLEAN, engine, output)
    if completed.returncode == 1:
        signature = output.split(":")[-1].strip() if ":" in output else output
        return ScanResult(INFECTED, engine, signature or "malware detected")
    return ScanResult(ERROR, engine, output or "scanner reported an error")


def describe_host() -> dict:
    """Reported by /health so an unscanned deployment is visible."""
    scanner = _scanner()
    return {
        "virus_scanning": scanner is not None,
        "engine": scanner[0] if scanner else None,
    }
