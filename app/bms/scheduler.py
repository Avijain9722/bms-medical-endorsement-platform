"""Background maintenance.

One job so far: the document retention purge. It runs inside the application
process rather than as an external timer, so a single-host BMS deployment needs
nothing scheduled at the operating-system level -- but it can be turned off
(`BMS_PURGE_INTERVAL_MINUTES=0`) for sites that prefer their own scheduler
driving `python3 -m bms.cli purge`.
"""

from __future__ import annotations

import asyncio
import logging

from .config import Settings, settings
from .db import session_scope
from .pipeline import purge_closed_cases

logger = logging.getLogger("bms.scheduler")


async def purge_loop(config: Settings | None = None, *, run_once: bool = False) -> None:
    """Run the retention purge on an interval until cancelled."""
    config = config or settings
    interval = max(0, config.purge_interval_minutes) * 60
    if not interval or not config.purge_enabled:
        logger.info("retention purge scheduler disabled")
        return

    while True:
        try:
            with session_scope() as session:
                result = purge_closed_cases(session, config=config, actor="scheduler")
            if result.cases:
                logger.info(
                    "retention purge removed %s document(s) and %s export(s) across %s case(s)",
                    result.files,
                    result.exports,
                    result.cases,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed sweep must not kill the loop
            logger.exception("retention purge failed; will retry next interval")

        if run_once:
            return
        await asyncio.sleep(interval)


def start(config: Settings | None = None) -> asyncio.Task | None:
    """Start the loop as a background task, if it is enabled."""
    config = config or settings
    if not config.purge_enabled or config.purge_interval_minutes <= 0:
        return None
    return asyncio.create_task(purge_loop(config), name="bms-retention-purge")
