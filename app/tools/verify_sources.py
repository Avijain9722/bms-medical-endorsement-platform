"""Verify the supplied source files are unmodified.

The portal workbooks, the BMS log template and the brand assets are controlled
inputs. Nothing in this repository may alter them, so CI checks their SHA-256
digests against the manifest BMS shipped in `00_START_HERE/SHA256SUMS.txt`.

Two severities:

* **Immutable** -- `01_BRAND_ASSETS`, `02_PORTAL_TEMPLATES`,
  `03_INTERNAL_LOG_TEMPLATE`. A change here fails the build. These files define
  the structural baseline every generated workbook is checked against; if one
  drifts, every fingerprint in the registry is silently invalidated.
* **Advisory** -- `00_START_HERE`, `04_DEVELOPMENT_EXAMPLES`,
  `05_TECHNICAL_REFERENCE`. Documentation and examples may legitimately be
  revised, so a change is reported but does not fail the build.

Replacing a template is a deliberate act: update the workbook, update
`SHA256SUMS.txt`, and re-run the preservation suite so the new baseline is
recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

IMMUTABLE_PREFIXES = ("01_BRAND_ASSETS", "02_PORTAL_TEMPLATES", "03_INTERNAL_LOG_TEMPLATE")

OK = "ok"
CHANGED = "changed"
MISSING = "missing"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_manifest(manifest: Path) -> list[tuple[str, str]]:
    entries = []
    for line in manifest.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        expected, _, name = line.partition("  ")
        entries.append((expected, name.lstrip("./")))
    return entries


def check(repo_root: Path) -> list[dict]:
    manifest = repo_root / "00_START_HERE" / "SHA256SUMS.txt"
    results = []
    for expected, name in parse_manifest(manifest):
        path = repo_root / name
        immutable = name.startswith(IMMUTABLE_PREFIXES)
        if not path.exists():
            status = MISSING
            actual = ""
        else:
            actual = sha256(path)
            status = OK if actual == expected else CHANGED
        results.append(
            {
                "name": name,
                "status": status,
                "immutable": immutable,
                "expected": expected,
                "actual": actual,
            }
        )
    return results


def to_markdown(results: list[dict]) -> str:
    failures = [r for r in results if r["immutable"] and r["status"] != OK]
    advisories = [r for r in results if not r["immutable"] and r["status"] != OK]

    lines = ["## Source integrity", ""]
    if failures:
        lines.append(f"**FAILED** -- {len(failures)} immutable source file(s) changed.")
    elif advisories:
        lines.append(f"**PASSED** with {len(advisories)} advisory change(s) in docs or examples.")
    else:
        lines.append(f"**PASSED** -- all {len(results)} supplied files match the manifest.")
    lines.append("")

    if failures or advisories:
        lines += ["| File | Severity | Status |", "| --- | --- | --- |"]
        for entry in failures + advisories:
            severity = "immutable" if entry["immutable"] else "advisory"
            lines.append(f"| `{entry['name']}` | {severity} | {entry['status']} |")
        lines.append("")

    counts = {
        "immutable verified": sum(1 for r in results if r["immutable"] and r["status"] == OK),
        "advisory verified": sum(1 for r in results if not r["immutable"] and r["status"] == OK),
    }
    lines.append(
        "Verified: "
        + ", ".join(f"{value} {key}" for key, value in counts.items())
        + f" (of {len(results)} manifest entries)."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[2])
    parser.add_argument("--out", help="write the Markdown report to this file as well as stdout")
    args = parser.parse_args(argv)

    results = check(Path(args.repo_root))
    report = to_markdown(results)
    print(report)
    if args.out:
        Path(args.out).write_text(report)

    broken = [r for r in results if r["immutable"] and r["status"] != OK]
    if broken:
        for entry in broken:
            print(
                f"::error file={entry['name']}::immutable source file {entry['status']}"
                " -- update SHA256SUMS.txt and re-run the preservation suite if this is intentional",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
