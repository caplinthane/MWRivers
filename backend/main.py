"""
Palmy River Watch — API layer.

This file wires the rest of the backend into HTTP endpoints. Here's how
the pieces fit together:

    hilltop_client.py  -> talks to Horizons' Hilltop Server (the upstream
                           data source), returns parsed Python objects
    poller.py          -> calls hilltop_client on a schedule, saves
                           results via database.py
    database.py        -> all the SQL lives here; this file never writes
                           raw SQL itself
    auth.py             -> the X-API-Key check used by the admin/debug
                           routes near the bottom of this file

Endpoints are grouped into two sections below: public read endpoints (no
auth — this is an open-data dashboard, not a private one) and admin/debug
endpoints (require the X-API-Key header; see auth.py and .env.example).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # reads ADMIN_API_KEY (and anything else) from backend/.env

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

import database
import hilltop_client
import poller
from auth import verify_admin_token
from sites import SITES


# ---------------------------------------------------------------------------
# App setup + background poller lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI calls this once on startup and once on shutdown (the code after
    `yield` runs on shutdown). We use it to prepare the database and start
    the background poller alongside the web server.
    """
    database.init_db()

    # Poll once immediately so there's real data as soon as the server is
    # reachable, instead of an empty dashboard for up to 15 minutes.
    await poller.poll_once()

    # Then keep polling forever in the background without blocking
    # requests — create_task starts it running and returns immediately.
    task = asyncio.create_task(poller.poll_loop())

    yield  # <- the app serves requests here, until it's shut down

    task.cancel()  # stop the background loop cleanly on shutdown


app = FastAPI(
    title="Palmy River Watch API",
    description="Live + historical river level / flow data for Palmerston "
    "North, sourced from Horizons Regional Council's public Hilltop Server "
    "and persisted locally in SQLite.",
    lifespan=lifespan,
)

# Wide-open CORS since this is a small public-data demo project — any
# website can call this API from a browser. Tighten allow_origins to your
# own domain if you deploy this somewhere that matters more.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Small shared helpers, so the endpoints below don't repeat themselves
# ---------------------------------------------------------------------------

def _get_site_or_404(site_key: str) -> dict:
    """Look up a configured site, or raise the 404 every endpoint needs."""
    cfg = SITES.get(site_key)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Unknown site '{site_key}'")
    return cfg


def _since_iso(*, hours: int | None = None, days: int | None = None) -> str:
    """Build an ISO timestamp for 'N hours/days ago', for SQL WHERE clauses."""
    delta = dt.timedelta(hours=hours) if hours is not None else dt.timedelta(days=days)
    return (dt.datetime.now() - delta).isoformat()


def _compute_trend(readings: list[dict]) -> str:
    """
    "rising", "falling", or "steady" — found by comparing the latest
    reading to whichever stored reading is closest to one hour earlier.

    We compare against ~1 hour ago rather than just the previous reading
    because river levels are naturally a bit noisy from one reading to the
    next; comparing over a longer window smooths that out so the trend
    reflects a real direction rather than sensor jitter.
    """
    if len(readings) < 2:
        return "steady"

    latest_time = dt.datetime.fromisoformat(readings[-1]["time"])
    latest_value = readings[-1]["value"]
    reference_time = latest_time - dt.timedelta(hours=1)

    # min() with "distance from target time" as the key finds whichever
    # stored reading is closest to exactly one hour ago, even if the
    # poller's timestamps don't land on a clean hour boundary.
    reference = min(
        readings,
        key=lambda r: abs((dt.datetime.fromisoformat(r["time"]) - reference_time).total_seconds()),
    )

    delta = latest_value - reference["value"]
    # A fixed threshold (e.g. "changed by more than 1") wouldn't work for
    # both a trickling stream (~1 l/s) and the main river (~50,000 l/s), so
    # the threshold scales with the current value instead: 1%, with a tiny
    # floor so it still behaves sensibly near zero.
    threshold = max(abs(latest_value) * 0.01, 0.001)

    if delta > threshold:
        return "rising"
    if delta < -threshold:
        return "falling"
    return "steady"


# ---------------------------------------------------------------------------
# Public endpoints — no auth required. This is an open-data dashboard,
# and putting a login in front of public river levels would defeat the
# point of it.
# ---------------------------------------------------------------------------

@app.get("/api/sites")
def list_sites():
    """Return the configured sites this app tracks (see sites.py)."""
    return [{"key": key, **cfg} for key, cfg in SITES.items()]


@app.get("/api/sites/{site_key}/data")
def site_data(site_key: str, hours: int = 48):
    """
    Recent readings + latest value + trend for one site, served from our
    own SQLite store (filled by the background poller) rather than
    fetched live from Horizons — keeps this fast, and keeps working even
    if Horizons' server is briefly slow or down.
    """
    cfg = _get_site_or_404(site_key)
    readings = database.get_history(site_key, _since_iso(hours=max(hours, 1)))

    if not readings:
        raise HTTPException(
            status_code=502,
            detail="No stored data yet for this site. The background poller "
            "may not have completed its first run — check server logs, or "
            "try POST /api/admin/poll-now.",
        )

    latest = readings[-1]
    response = {
        "key": site_key,
        "label": cfg["label"],
        "measurement": cfg["measurement"],
        "unit": latest["unit"],
        "trend": _compute_trend(readings),
        "latest": {"time": latest["time"], "value": latest["value"]},
        "series": [{"time": r["time"], "value": r["value"]} for r in readings],
    }

    rainfall_key = cfg.get("rainfall_key")
    if rainfall_key:
        rain_readings = database.get_history(rainfall_key, _since_iso(hours=max(hours, 1)))
        if rain_readings:
            response["rainfall"] = {
                "site_name": cfg["rainfall_site"],
                "co_located": cfg["rainfall_co_located"],
                "unit": rain_readings[-1]["unit"],
                "series": [{"time": r["time"], "value": r["value"]} for r in rain_readings],
            }

    return response


@app.get("/api/sites/{site_key}/stats")
def site_stats(site_key: str, days: int = 7):
    """Min / max / avg / count over the last N days of stored data."""
    cfg = _get_site_or_404(site_key)
    stats = database.get_stats(site_key, _since_iso(days=max(days, 1)))
    if not stats or stats["count"] == 0:
        raise HTTPException(status_code=404, detail="No stored data for this period yet.")
    return {"key": site_key, "label": cfg["label"], "period_days": days, **stats}


@app.get("/api/sites/{site_key}/daily")
def site_daily(site_key: str, days: int = 30):
    """Day-by-day min/max/avg rollup over the last N days — this is what
    powers the frontend's 30-day trend chart."""
    cfg = _get_site_or_404(site_key)
    rollup = database.get_daily_rollup(site_key, _since_iso(days=max(days, 1)))
    return {"key": site_key, "label": cfg["label"], "days": rollup}


@app.get("/api/health")
def health():
    """Trivial endpoint for uptime checks — just confirms the server is up."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Admin/debug endpoints — require the X-API-Key header (see auth.py).
# Everything above this line is intentionally open; everything below it
# either changes server state or exposes internals, so it's locked down.
# ---------------------------------------------------------------------------

@app.post("/api/admin/poll-now", dependencies=[Depends(verify_admin_token)])
async def poll_now():
    """Trigger a poll immediately instead of waiting up to 15 minutes for
    the next scheduled one — mainly useful while developing."""
    await poller.poll_once()
    return {"status": "polled"}


@app.get("/api/debug/raw/{site_key}", dependencies=[Depends(verify_admin_token)])
def debug_raw(site_key: str):
    """
    Return Horizons' raw XML response for one site, completely unparsed —
    useful for confirming exactly what the upstream server is sending back
    when something looks wrong (e.g. an unexpected site/measurement name).
    """
    cfg = _get_site_or_404(site_key)
    try:
        url, status, text = hilltop_client.fetch_raw(cfg["site_name"], cfg["measurement"])
    except hilltop_client.HilltopError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return PlainTextResponse(content=f"URL requested:\n{url}\n\nStatus: {status}\n\n{text}")
