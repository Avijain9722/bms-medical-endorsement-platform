#!/usr/bin/env python3
"""Check that an offline wheelhouse is complete for a Windows install.

This exists because of a real failure. The bundle is built on Linux with
`pip download --platform win_amd64`, and that flag chooses which wheel *tags*
are acceptable -- it does not change the environment markers pip evaluates. A
requirement written

    colorama ; platform_system == "Windows"

is therefore tested against the *building* machine, decided to be False, and
silently left out. The bundle looks complete, and the BMS host fails at install
time with:

    ERROR: Could not find a version that satisfies the requirement colorama;
    platform_system == "Windows" (from click)

Worse, the obvious verification -- installing from the wheelhouse with
`--no-index` on the build machine -- passes, because it skips the same
requirement for the same wrong reason. Only a Windows machine, or this check,
notices.

So this reads each wheel's own metadata and asks: which requirements would a
Windows host activate that are not in the folder? It needs no network and no
Windows.

    python3 tools/check_wheelhouse.py ../deploy/wheelhouse

Exits non-zero if anything is missing, so it can gate a release.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

# Markers that a Windows host satisfies and this build host does not.
WINDOWS_MARKER = re.compile(
    r'platform_system\s*==\s*"Windows"|sys_platform\s*==\s*"win32"|os_name\s*==\s*"nt"'
)
# Requirements behind an optional extra are not installed unless that extra is
# requested, so their absence is not a defect.
EXTRA_MARKER = re.compile(r"extra\s*==")


def distribution_name(value: str) -> str:
    return re.split(r"[<>=!~\[; ]", value.strip())[0].lower().replace("_", "-")


def wheel_metadata(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith(".dist-info/METADATA"):
                return archive.read(name).decode("utf-8", "replace")
    return ""


def missing_for_windows(wheelhouse: Path) -> list[tuple[str, str, str]]:
    wheels = sorted(wheelhouse.glob("*.whl"))
    present = {w.name.split("-")[0].lower().replace("_", "-") for w in wheels}

    gaps: list[tuple[str, str, str]] = []
    for wheel in wheels:
        owner = wheel.name.split("-")[0]
        for line in wheel_metadata(wheel).splitlines():
            if not line.startswith("Requires-Dist:"):
                continue
            body = line.split(":", 1)[1].strip()
            if ";" not in body:
                continue
            requirement, marker = body.split(";", 1)
            if EXTRA_MARKER.search(marker) or not WINDOWS_MARKER.search(marker):
                continue
            name = distribution_name(requirement)
            if name not in present:
                gaps.append((name, owner, marker.strip()))
    return gaps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheelhouse", type=Path, help="folder of .whl files")
    args = parser.parse_args()

    if not args.wheelhouse.is_dir():
        print(f"no such folder: {args.wheelhouse}", file=sys.stderr)
        return 2

    wheels = sorted(args.wheelhouse.glob("*.whl"))
    if not wheels:
        print(f"no wheels in {args.wheelhouse}", file=sys.stderr)
        return 2

    gaps = missing_for_windows(args.wheelhouse)
    print(f"{len(wheels)} wheels in {args.wheelhouse}")
    if not gaps:
        print("PASSED -- every Windows-gated requirement is present.")
        return 0

    print(f"FAILED -- {len(gaps)} requirement(s) a Windows host needs are absent:\n")
    for name, owner, marker in gaps:
        print(f"  {name:20} required by {owner}  ({marker})")
    print(
        "\nAdd them explicitly. `--platform win_amd64` will not fetch them: it "
        "selects wheel tags, it does not evaluate markers as Windows."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
