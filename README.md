# 🌊 Palmy River Watch

A small full-stack app showing live river and stream flow around
**Palmerston North, New Zealand**, built on
[Horizons Regional Council's public environmental data](https://www.horizons.govt.nz/environment/natural-hazards-and-flood-management/environmental-data-at-horizons)
(a free, no-API-key Hilltop Server).

![Screenshot of Palmy River Watch showing the gauge location map and three site cards with live readings, trend indicators, and 30-day history charts](docs/screenshot.png)

- **Backend:** Python + FastAPI + SQLite. Polls Horizons' Hilltop Server in
  the background, builds its own local history over time, and correlates
  each river gauge with a nearby rainfall station.
- **Frontend:** Plain HTML/CSS/JS + Chart.js + Leaflet (all vendored
  locally). No build step, no framework.

> ⚠️ **This is a personal/portfolio project, not an official flood warning
> service.** For real flood risk information, alerts, or emergencies, use
> Horizons' own river alert system or call the Waterline on **0508 4 FLOOD**.

## Why I built this

I wanted a portfolio project that wasn't another to-do list or weather
widget — something using real, local, public data with an actual reason to
exist. Horizons Regional Council publishes live river and rainfall
telemetry for the whole Manawatū region with no API key required, which
turned out to be a great excuse to build a proper small full-stack app:
a background poller, a real (if tiny) database, a JSON API, and a frontend
that doesn't lean on a framework to look considered.

## Monitored sites

Configured in [`backend/sites.py`](backend/sites.py):

- **Manawatū River at Teachers College** — the main river gauge in central
  Palmerston North
- **Turitea Stream at Ngahere Park** — stream through the eastern suburbs
- **Kahuterawa Stream at Johnstons Rata** — stream draining the Tararua
  foothills SW of the city

Add more by looking up site/measurement names against the live server:

```
https://hilltopserver.horizons.govt.nz/data.hts?Service=Hilltop&Request=SiteList
https://hilltopserver.horizons.govt.nz/data.hts?Service=Hilltop&Request=MeasurementList&Site=<site name>
```

Each site also has an associated rainfall station (see `rainfall_site` in
`sites.py`), checked against Horizons' live data rather than guessed:
Turitea and Kahuterawa each have a rain gauge in their own catchment;
Manawatu at Teachers College doesn't have one on-site, so it reuses the
nearest station as a proxy — the frontend labels this honestly rather
than implying an exact match.

## Running it locally

### 1. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
cp .env.example .env   # then edit .env and set a real ADMIN_API_KEY (see below)
uvicorn main:app --reload --port 8000
```

Check it's working: open http://localhost:8000/api/sites — you should see
JSON listing the three configured sites. Then try
http://localhost:8000/api/sites/manawatu-teachers-college/data for a live
reading.

### 2. Frontend

The frontend is static — no build step. Easiest way to serve it locally
(so the browser's fetch calls behave nicely):

```bash
cd frontend
python3 -m http.server 5500
```

Then open http://localhost:5500 in your browser. It talks to the backend
at `http://localhost:8000` by default — override this by setting
`window.PALMY_API_BASE` before `app.js` loads if you deploy the backend
elsewhere.

## How the data flows

1. **`poller.py`** runs in the background (started from `main.py`'s FastAPI
   lifespan handler) and, every 15 minutes, polls every configured river
   site plus every *unique* rainfall station (two sites can share one
   station — see `rainfall_key` in `sites.py` — so it's only fetched
   once), storing new readings in SQLite (`database.py`) via
   `INSERT OR IGNORE` on a `(site, timestamp)` primary key, which makes
   re-polling overlapping windows safe. One poll also runs immediately on
   startup, so there's data as soon as the server is up.
2. **`main.py`** (FastAPI) serves everything from that local SQLite store
   rather than hitting Horizons on every request — faster, and the app
   keeps working even if Horizons' server is briefly down.
3. **`app.js`** fetches from your backend and renders a 48-hour chart (with
   a rainfall bar overlay, if configured) and a 30-day trend chart per
   site with Chart.js, refreshing every 5 minutes.
4. **`map-init.js`** renders a real Leaflet + OpenStreetMap map, with a
   marker for every site in the same `/api/sites` response the cards use
   — there's no separate hardcoded map data to keep in sync.

Because the app builds its own history over time, the longer you leave it
running, the more useful `/stats` and `/daily` become — day one, they'll
only cover a few hours of data (the frontend shows a "not enough history
yet" message on the 30-day chart until there's at least 2 days stored).

**One deliberate exception to "no CDN, everything vendored":** map tiles.
Chart.js and Leaflet's own code are vendored in `frontend/vendor/`, but
the actual map imagery is fetched live from OpenStreetMap's tile servers
each time the map renders — there isn't really a way to vendor a whole
mapping service, and OSM tiles are free and don't require a key.

## API endpoints

| Endpoint | What it returns |
|---|---|
| `GET /api/sites` | The configured sites |
| `GET /api/sites/{key}/data?hours=48` | Recent series + latest reading + trend + rainfall (if configured), from local storage |
| `GET /api/sites/{key}/stats?days=7` | Min / max / avg / count over N days |
| `GET /api/sites/{key}/daily?days=30` | Day-by-day min/max/avg rollup |
| `POST /api/admin/poll-now` 🔒 | Manually trigger a poll (skip the 15-min wait while developing) |
| `GET /api/debug/raw/{key}` 🔒 | Raw Hilltop XML response, unparsed — for troubleshooting |

🔒 = requires an `X-API-Key` header. Everything else is intentionally open —
this is a public-data dashboard, not a private one, so the only endpoints
worth gating are the one that mutates state and the one that leaks
internals.

### Setting up the admin key

```bash
cp backend/.env.example backend/.env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"  # generate a real secret
# paste it into backend/.env as ADMIN_API_KEY=...
```

`.env` is gitignored — never commit your real key. If `ADMIN_API_KEY` isn't
set at all, the protected endpoints refuse every request (fail closed)
rather than silently letting everyone through.

Calling a protected endpoint:
```bash
curl -X POST http://localhost:8000/api/admin/poll-now \
  -H "X-API-Key: your-secret-here"
```

Since the frontend is a static site with fully visible source, never embed
this key in `app.js` — it's meant for your own use (curl, a script, a cron
job), not for the browser to send on your behalf.

## Ideas to extend this

- **Rainfall on the 30-day chart too:** currently the rain overlay is only
  on the 48-hour chart — a daily-rollup version (rain totals per day
  alongside the min/max/avg flow band) would need its own aggregation in
  `database.py`.
- **Threshold alerting:** once you've picked sensible reference levels
  for a site (cross-check against Horizons' own published info, don't
  invent numbers), notify when a level is trending up fast.
- **More sites:** the Hilltop server covers the whole Manawatū-Whanganui
  region — Whanganui, Rangitīkei, Tararua, Horowhenua are all in there too.
- **Deploy it:** e.g. backend on Render/Fly.io/a small VPS (mount a
  persistent volume for `river_watch.db`), frontend on GitHub Pages or
  Netlify (update `PALMY_API_BASE` accordingly).

## Data source & credit

All environmental data is sourced live from Horizons Regional Council's
Hilltop Server (`hilltopserver.horizons.govt.nz`), made publicly available
by the council. This project is not affiliated with or endorsed by
Horizons Regional Council.

Map tiles © [OpenStreetMap](https://www.openstreetmap.org/copyright)
contributors, rendered with [Leaflet](https://leafletjs.com/).

## License

MIT — see [LICENSE](LICENSE).
