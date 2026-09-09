"""
Minimal client for Horizons Regional Council's public Hilltop Server API.

No API key required. Docs: the server is self-describing — query with
Request=SiteList, Request=MeasurementList, Request=CollectionList, or
Request=GetData against the base URL below.

Hilltop's GetData response is XML shaped roughly like:

<Hilltop>
  <Agency>Horizons Regional Council</Agency>
  <Measurement SiteName="..." DataType="...">
    <DataSource ...>
      <ItemInfo ItemNumber="1">
        <ItemName>...</ItemName>
        <Units>...</Units>
      </ItemInfo>
    </DataSource>
    <Data DateFormat="Calendar" NumItems="1">
      <E><T>2024-01-01T12:00:00</T><I1>1.234</I1></E>
      ...
    </Data>
  </Measurement>
</Hilltop>

Some older/alternate Hilltop deployments vary slightly in tag naming, so
the parser below is deliberately tolerant: it looks for any element named
"E" containing a "T" (timestamp) child and takes the first "I*" child as
the value, rather than hard-coding "I1".
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

import httpx

BASE_URL = "https://hilltopserver.horizons.govt.nz/data.hts"
TIMEOUT = 20.0
HEADERS = {
    # Some servers behave differently (or block) requests with no/odd
    # User-Agent. Pretend to be a normal browser-ish client.
    "User-Agent": "Mozilla/5.0 (compatible; PalmyRiverWatch/1.0)",
    "Accept": "application/xml,text/xml,*/*",
}


@dataclass
class Reading:
    """One (timestamp, value) point from Hilltop — e.g. a single flow reading."""

    time: dt.datetime
    value: float


class HilltopError(RuntimeError):
    """Raised for anything that goes wrong talking to or parsing Hilltop —
    network failures, malformed XML, or an <Error> element in the response."""

    pass


def _build_url(**params) -> str:
    """Build a Hilltop request URL. Pass query params as keyword args,
    e.g. _build_url(Request="GetData", Site="...", Measurement="...")."""
    query = {"Service": "Hilltop", **params}
    # IMPORTANT: use quote_via=quote (produces %20 for spaces), NOT the
    # default quote_plus (produces "+"). This Hilltop server does not
    # decode "+" back to a space — it treats it as a literal character,
    # which silently breaks every Site/Measurement lookup that contains
    # a space (i.e. almost all of them).
    return f"{BASE_URL}?{urlencode(query, quote_via=quote)}"


def _get(url: str) -> ET.Element:
    """Fetch a URL and parse it as XML, wrapping any failure in HilltopError
    so callers only ever need to catch one exception type."""
    try:
        resp = httpx.get(
            url, timeout=TIMEOUT, headers=HEADERS, follow_redirects=True
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HilltopError(f"Request to Hilltop server failed: {exc}") from exc

    try:
        return ET.fromstring(resp.content)
    except ET.ParseError as exc:
        # Include a snippet of the raw response so failures are debuggable
        # without needing to reproduce the request separately.
        snippet = resp.text[:300].replace("\n", " ")
        raise HilltopError(
            f"Could not parse Hilltop XML response: {exc}. "
            f"Response started with: {snippet!r}"
        ) from exc


def get_site_list() -> list[str]:
    """Return every site name the server knows about."""
    root = _get(_build_url(Request="SiteList"))
    return [el.text for el in root.iter("SiteName") if el.text]


def get_measurement_list(site: str) -> list[str]:
    """Return every measurement available for a given site."""
    root = _get(_build_url(Request="MeasurementList", Site=site))
    return [el.text for el in root.iter("Measurement") if el.text]


def get_data(
    site: str,
    measurement: str,
    hours: int = 48,
) -> tuple[list[Reading], str | None]:
    """
    Fetch a time series for one site + measurement, covering the last
    `hours` hours up to now.

    Returns (readings, units) — units comes straight from the server's
    <Units> element when present, so the frontend can show real units
    (e.g. "m3/s" or "m") instead of the raw internal measurement name.

    We compute explicit absolute timestamps here rather than relying on
    relative keyword syntax (e.g. "Today-1day") — that syntax isn't
    reliably supported by this server and was silently ignored, causing
    it to return its entire historical archive instead of a recent
    window. "Now" alone is fine as the upper bound.
    """
    now = dt.datetime.now()
    from_dt = now - dt.timedelta(hours=hours)
    from_str = from_dt.strftime("%Y-%m-%d %H:%M:%S")

    root = _get(
        _build_url(
            Request="GetData",
            Site=site,
            Measurement=measurement,
            From=from_str,
            To="Now",
        )
    )

    error_el = root.find(".//Error")
    if error_el is not None:
        raise HilltopError(f"Hilltop server error: {error_el.text}")

    units_el = root.find(".//Units")
    units = units_el.text if units_el is not None else None

    readings: list[Reading] = []
    for e in root.iter("E"):
        # Each <E> is one reading: <E><T>timestamp</T><I1>value</I1></E>.
        # We don't hard-code "I1" because some Hilltop deployments use I2,
        # I3, etc. for different data types — so we just take whichever
        # child's tag starts with "I".
        t_el = e.find("T")
        if t_el is None or not t_el.text:
            continue  # no timestamp — skip, can't use this reading
        value_el = next(
            (child for child in e if child.tag.startswith("I")), None
        )
        if value_el is None or not value_el.text:
            continue  # no value — skip
        try:
            timestamp = dt.datetime.fromisoformat(t_el.text)
            value = float(value_el.text)
        except ValueError:
            continue  # malformed timestamp/number — skip rather than crash
        readings.append(Reading(time=timestamp, value=value))

    readings.sort(key=lambda r: r.time)
    return readings, units


def fetch_raw(site: str, measurement: str, hours: int = 24) -> tuple[str, int, str]:
    """
    Fetch Horizons' response for one site/measurement without parsing it —
    used by the /api/debug/raw endpoint so you can see exactly what the
    upstream server sent back. Returns (url_requested, status_code, body_text).
    """
    now = dt.datetime.now()
    from_str = (now - dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    url = _build_url(
        Request="GetData", Site=site, Measurement=measurement, From=from_str, To="Now"
    )
    try:
        resp = httpx.get(url, timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise HilltopError(f"Request to Hilltop server failed: {exc}") from exc
    return url, resp.status_code, resp.text
