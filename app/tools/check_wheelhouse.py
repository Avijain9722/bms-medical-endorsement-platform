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
Windows host activate that are not in the folder? When a target Python version
is supplied, it also verifies the wheel tags. It needs no network and no
Windows.

    python3 tools/check_wheelhouse.py ../deploy/wheelhouse --python-version 3.14

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


def _cpython_version(tag: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"cp(\d)(\d+)", tag)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def wheel_supports_target(path: Path, python_version: str, platform: str) -> bool:
    """Return whether a wheel filename declares support for the target.

    Platform-specific wheels are not necessarily compiled extensions. Ruff,
    for example, correctly ships as ``py3-none-win_amd64``: it contains a
    Windows executable but no CPython ABI. Treating every non-``none-any``
    wheel as compiled caused the release bundle to fail after pip had selected
    a valid artifact.
    """
    parts = path.stem.rsplit("-", 3)
    if len(parts) != 4:
        return False
    python_tags, abi_tags, platform_tags = (set(value.split(".")) for value in parts[-3:])

    digits = python_version.replace(".", "")
    target_cpython = f"cp{digits}"
    target_python = f"py{digits}"
    target_major = f"py{python_version.split('.', 1)[0]}"

    if platform not in platform_tags and "any" not in platform_tags:
        return False
    if "none" in abi_tags and python_tags & {target_cpython, target_python, target_major}:
        return True
    if target_cpython in python_tags and target_cpython in abi_tags:
        return True
    if "abi3" in abi_tags:
        target = _cpython_version(target_cpython)
        supported = (_cpython_version(tag) for tag in python_tags)
        return any(version is not None and target is not None and version <= target for version in supported)
    return False


def incompatible_wheels(
    wheelhouse: Path, python_version: str, platform: str
) -> list[str]:
    return [
        wheel.name
        for wheel in sorted(wheelhouse.glob("*.whl"))
        if not wheel_supports_target(wheel, python_version, platform)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheelhouse", type=Path, help="folder of .whl files")
    parser.add_argument(
        "--python-version",
        help="also require wheel tags compatible with this version, for example 3.14",
    )
    parser.add_argument("--platform", default="win_amd64", help="target wheel platform")
    args = parser.parse_args()

    if not args.wheelhouse.is_dir():
        print(f"no such folder: {args.wheelhouse}", file=sys.stderr)
        return 2

    wheels = sorted(args.wheelhouse.glob("*.whl"))
    if not wheels:
        print(f"no wheels in {args.wheelhouse}", file=sys.stderr)
        return 2

    gaps = missing_for_windows(args.wheelhouse)
    incompatible = (
        incompatible_wheels(args.wheelhouse, args.python_version, args.platform)
        if args.python_version
        else []
    )
    print(f"{len(wheels)} wheels in {args.wheelhouse}")
    if not gaps and not incompatible:
        detail = " and every wheel supports the target" if args.python_version else ""
        print(f"PASSED -- every Windows-gated requirement is present{detail}.")
        return 0

    if gaps:
        print(f"FAILED -- {len(gaps)} requirement(s) a Windows host needs are absent:\n")
        for name, owner, marker in gaps:
            print(f"  {name:20} required by {owner}  ({marker})")
        print(
            "\nAdd them explicitly. `--platform win_amd64` will not fetch them: it "
            "selects wheel tags, it does not evaluate markers as Windows."
        )
    if incompatible:
        print(f"FAILED -- {len(incompatible)} wheel(s) do not support the target:")
        for filename in incompatible:
            print(f"  {filename}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
