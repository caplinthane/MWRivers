"""
Simple API-key auth, scoped to just the admin/debug endpoints.

The public GET endpoints (site data, stats, daily rollups) stay open on
purpose — this is an open-data dashboard, and putting a login in front of
public river levels would defeat the point. This token only protects the
handful of endpoints that either mutate state (triggering a poll) or leak
internals (raw upstream responses) — the ones actually worth locking down
if you deploy this somewhere public.

Set ADMIN_API_KEY in a .env file (copy .env.example) or as a real
environment variable. If it isn't set, protected endpoints refuse every
request rather than silently letting everyone through — fail closed, not
open.

Note: because the frontend is a static site with visible source, this key
is meant for your own use (curl, a cron job, a admin script) — never embed
it in frontend JS, since anyone could read it out of the page source.
"""

from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_admin_token(provided: str | None = Security(_api_key_header)) -> None:
    """
    A FastAPI dependency — attach it to a route with
    `dependencies=[Depends(verify_admin_token)]` and FastAPI runs this
    automatically before the route's own code, for every request.

    `Security(_api_key_header)` is how the X-API-Key header value gets
    into `provided` here without main.py needing to extract it manually.
    Returning normally means "request allowed"; raising HTTPException
    stops the request before it ever reaches the route.
    """
    expected = os.environ.get("ADMIN_API_KEY")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Server misconfigured: ADMIN_API_KEY is not set. See .env",
        )
    # constant-time comparison so response timing can't leak the correct key
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401, detail="Missing or invalid X-API-Key header."
        )
