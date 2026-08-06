"""Excel recalculation for workbooks that carry live formulas.

Two of the registered templates need Excel itself before hand-off:

* **the BMS log**, whose `TAT` calculated column, `TODAY()`, ageing formulas and
  SUMMARY dashboard only evaluate when Excel opens the file;
* **the Daman workbook**, whose own macro fills the `Is Valid` column.

The surgical writer produces a structurally perfect file with the formulas
intact, but a formula that has never been evaluated has no cached value, so
another reader may show it blank until the file is opened once.

This runs that pass on Windows through COM, and -- crucially -- reports honestly
when it cannot. A file that was not recalculated is marked as such rather than
being presented as finished.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Templates whose output should be opened by Excel before it is handed over.
NEEDS_RECALC = frozenset({"bms.log.2026", "daman.addition.v1"})


@dataclass(frozen=True)
class RecalcResult:
    performed: bool
    engine: str
    detail: str

    @property
    def pending(self) -> bool:
        return not self.performed


def excel_available() -> tuple[bool, str]:
    """Whether a local Excel automation route exists on this host."""
    if platform.system() != "Windows":
        return False, f"host is {platform.system()}, and Excel automation requires Windows"
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return False, "pywin32 is not installed"
    return True, "Excel COM automation available"


def _recalculate_with_com(path: Path) -> RecalcResult:  # pragma: no cover - Windows only
    """Open, recalculate and save in place, leaving macros and protection alone.

    `Open` is given the workbook's own format and `AutomationSecurity` is set to
    disable macros on open: the Daman project must survive in the file, but it
    must not execute on a server. The workbook's own error-check macro is run by
    the BMS user on their workstation, not here.
    """
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    excel = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        # 3 = msoAutomationSecurityForceDisable
        excel.AutomationSecurity = 3
        workbook = excel.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=False)
        excel.CalculateFullRebuild()
        workbook.Save()
        workbook.Close(SaveChanges=True)
        return RecalcResult(True, "excel_com", "recalculated and saved by Excel")
    finally:
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()


def recalculate(path: str | Path, *, template_key: str | None = None) -> RecalcResult:
    """Recalculate a generated workbook if this host can, and say so either way."""
    path = Path(path)
    if template_key is not None and template_key not in NEEDS_RECALC:
        return RecalcResult(True, "not_required", "this template carries no live formulas")

    available, reason = excel_available()
    if not available:
        return RecalcResult(
            False,
            "unavailable",
            f"formulas were written but not evaluated: {reason}. "
            "The file is complete and opens correctly; Excel will calculate on first open.",
        )
    return _recalculate_with_com(path)  # pragma: no cover - Windows only


def describe_host() -> dict:
    """Reported by /health so the gap is visible before anyone relies on it."""
    available, reason = excel_available()
    return {
        "excel_recalculation": available,
        "reason": reason,
        "templates_requiring_recalculation": sorted(NEEDS_RECALC),
    }
