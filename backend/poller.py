"""
Background poller: periodically fetches recent data for every configured
site and stores new readings in SQLite.

Runs as an asyncio task started from main.py's lifespan handler, not as a
separate process — fine for a small single-instance app like this.
"""

from __future__ import annotations

import asyncio
import logging

import database
import hilltop_client
from sites import SITES

logger = logging.getLogger("poller")
logging.basicConfig(level=logging.INFO)

POLL_INTERVAL_SECONDS = 15 * 60  # 15 minutes
POLL_WINDOW_HOURS = 3  # small overlap so we never miss a reading between polls


async def poll_once() -> None:
    """
    Poll every configured site once, then every unique rainfall station
    once. Failures on one site/station don't stop the others — each is
    wrapped in its own try/except.
    """
    for key, cfg in SITES.items():
        try:
            # hilltop_client uses a sync HTTP client (httpx.get) — run it in
            # a thread so it doesn't block the asyncio event loop that's
            # also serving API requests.
            readings, unit = await asyncio.to_thread(
                hilltop_client.get_data,
                cfg["site_name"],
                cfg["measurement"],
                POLL_WINDOW_HOURS,
            )
            inserted = database.store_readings(key, readings, unit)
            logger.info(
                "[%s] fetched %d readings, %d new", key, len(readings), inserted
            )
        except hilltop_client.HilltopError as exc:
            logger.warning("[%s] poll failed: %s", key, exc)

    await _poll_rainfall_stations()


async def _poll_rainfall_stations() -> None:
    """
    Poll each unique rainfall station once. Built as a separate pass from
    the river sites above because two river sites can share the same
    rainfall station (see sites.py's rainfall_key docs) — fetching by
    unique station rather than by river site avoids polling Horizons for
    the same rainfall data twice in one cycle.
    """
    seen: dict[str, tuple[str, str]] = {}
    for cfg in SITES.values():
        rainfall_key = cfg.get("rainfall_key")
        if rainfall_key:
            seen[rainfall_key] = (cfg["rainfall_site"], cfg["rainfall_measurement"])

    for rainfall_key, (site_name, measurement) in seen.items():
        try:
            readings, unit = await asyncio.to_thread(
                hilltop_client.get_data, site_name, measurement, POLL_WINDOW_HOURS
            )
            inserted = database.store_readings(rainfall_key, readings, unit)
            logger.info(
                "[%s] (rainfall) fetched %d readings, %d new",
                rainfall_key, len(readings), inserted,
            )
        except hilltop_client.HilltopError as exc:
            logger.warning("[%s] (rainfall) poll failed: %s", rainfall_key, exc)


async def poll_loop() -> None:
    """
    Runs forever: wait POLL_INTERVAL_SECONDS, poll, repeat. Started once
    from main.py's lifespan handler and left running for the life of the
    server; main.py cancels this task on shutdown.

    Note the wait happens *before* the first poll here — that's
    deliberate, since main.py already calls poll_once() once by itself
    right before starting this loop, so we don't want to double up.
    """
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        await poll_once()
