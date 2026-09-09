// =============================================================================
// Palmy River Watch — frontend
//
// No framework, no build step: this file fetches JSON from the backend
// (see backend/main.py) and builds the DOM by hand. Structure:
//   1. Config / constants
//   2. Small pure helpers (formatting, DOM lookups)
//   3. Rendering (turn API data into HTML + Chart.js charts)
//   4. Data loading (fetch + wire rendering together)
//   5. Entry point
// =============================================================================

// Point this at your running backend. Override by setting
// window.PALMY_API_BASE in a <script> before this file loads, if you
// deploy the backend somewhere other than localhost.
const API_BASE = window.PALMY_API_BASE || "http://localhost:8000";

// If the latest reading is older than this, the UI flags it as possibly
// delayed instead of silently showing stale data as if it were live.
const STALE_AFTER_MINUTES = 60;

// How far back the "last 30 days" chart asks the API for.
const DAILY_LOOKBACK_DAYS = 30;

// Below this many days of stored history, we show a "not enough data
// yet" message instead of a mostly-empty chart.
const MIN_DAYS_FOR_DAILY_CHART = 2;

// Display text + arrow glyph for each trend value the API can return.
const TREND_META = {
    rising: { label: "Rising", arrow: "\u2191" },
    falling: { label: "Falling", arrow: "\u2193" },
    steady: { label: "Steady", arrow: "\u2192" },
};

// Chart line/fill colors, keyed by trend — so a rising river's chart is
// drawn in the same amber as its trend badge, etc. Rainfall bars use a
// separate fixed blue-grey (--rain in style.css) since rain isn't itself
// "trending" the same way flow is.
const CHART_COLORS = {
    rising: { line: "#B8791F", fill: "rgba(184,121,31,0.12)" },
    falling: { line: "#3F8361", fill: "rgba(63,131,97,0.12)" },
    steady: { line: "#1C7C93", fill: "rgba(28,124,147,0.12)" },
};
const RAIN_COLOR = "rgba(107,127,163,0.55)";

// Shared Chart.js tooltip styling so charts match the rest of the theme
// instead of Chart.js's default white bubble / system font.
const TOOLTIP_STYLE = {
    backgroundColor: "#1D3461",
    titleFont: { family: "IBM Plex Mono", size: 11 },
    bodyFont: { family: "IBM Plex Mono", size: 11 },
    cornerRadius: 2,
    displayColors: false,
    padding: 8,
};

// Shared axis styling, spread into both charts' x/y scale options below.
const AXIS_STYLE = {
    ticks: { color: "#55605C", font: { family: "IBM Plex Mono", size: 10 } },
    grid: { color: "#E4E8E5" },
};

const sitesEl = document.getElementById("sites");

// -----------------------------------------------------------------------
// Small helpers
// -----------------------------------------------------------------------

/**
 * Fetch a URL and parse it as JSON, throwing a readable Error on failure.
 * FastAPI error responses look like {"detail": "..."} — we surface that
 * message directly rather than a generic "response not ok".
 */
async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed: ${res.status}`);
    }
    return res.json();
}

/** Turn an ISO timestamp into "3m ago" / "2h ago" / "1d ago" style text. */
function relativeTime(isoString) {
    const diffMs = Date.now() - new Date(isoString).getTime();
    const minutes = Math.round(diffMs / 60000);
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.round(hours / 24)}d ago`;
}

/** Read a card's site name back out of its own heading — used for the
 * 48-hour chart's aria-label, so it doesn't need the name passed in. */
function siteLabel(card) {
    return card.querySelector("h2")?.textContent || "site";
}

/**
 * Format the big headline number sensibly for its scale: the main river
 * can read in the tens of thousands (l/s), while a small stream reads in
 * the hundreds — a single fixed "always 2 decimals" rule looks odd at
 * both ends (e.g. "55601.19" has false precision; thousands with no
 * separator are hard to scan). So: values of 1000+ get comma-grouped
 * with no decimals, smaller values keep 2 decimals for real precision.
 */
function formatValue(value) {
    if (Math.abs(value) >= 1000) {
        return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
    }
    return value.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}

// -----------------------------------------------------------------------
// Rendering
// -----------------------------------------------------------------------

/**
 * Build the empty card shell for one site, with a loading skeleton in
 * place of real data. Called once per site on page load, before any
 * network request has resolved — renderLoaded() fills it in later.
 */
function makeCard(site) {
    const card = document.createElement("section");
    card.className = "card";
    card.id = `card-${site.key}`;
    card.setAttribute("aria-label", `${site.label} river gauge data`);
    card.innerHTML = `
    <h2>${site.label}</h2>
    <p class="desc">${site.description}</p>
    <div class="body">
      <div class="skeleton-line skeleton-value" aria-hidden="true"></div>
      <div class="skeleton-line skeleton-chart" aria-hidden="true"></div>
    </div>
  `;
    return card;
}

/**
 * Fill in a card with real data: current value, trend badge, freshness
 * text, and the 48-hour chart (with a rainfall overlay, if the site has
 * a rainfall station configured). Then kicks off loadDailyChart() for
 * the second (30-day) chart, since that's a separate API call.
 */
function renderLoaded(card, data) {
    const trend = TREND_META[data.trend] || TREND_META.steady;
    card.classList.remove("trend-rising", "trend-falling", "trend-steady");
    card.classList.add(`trend-${data.trend || "steady"}`);

    const body = card.querySelector(".body");
    const minutesOld = Math.round((Date.now() - new Date(data.latest.time).getTime()) / 60000);
    const isStale = minutesOld > STALE_AFTER_MINUTES;
    const unit = data.unit || data.measurement;
    const hasRain = data.rainfall && data.rainfall.series && data.rainfall.series.length > 0;

    body.innerHTML = `
    <div class="latest">
      <span class="value">${formatValue(data.latest.value)}</span>
      <span class="unit">${unit}</span>
      <span class="trend-label" aria-label="Trend: ${trend.label}">${trend.arrow} ${trend.label}</span>
    </div>
    <p class="freshness ${isStale ? "stale" : ""}">
      ${isStale ? "⚠ data may be delayed — " : "updated "}${relativeTime(data.latest.time)}
    </p>
    <canvas aria-label="Chart of ${siteLabel(card)} over the last 48 hours${hasRain ? ", with rainfall overlay" : ""}" role="img"></canvas>
    ${hasRain ? `<p class="freshness">🌧 + rainfall at ${data.rainfall.site_name}${data.rainfall.co_located ? "" : " (nearby, not on-site)"}</p>` : ""}
    <p class="section-label">Last 30 days</p>
    <div class="long-term"><canvas aria-label="30-day trend"></canvas></div>
  `;

    const colors = CHART_COLORS[data.trend] || CHART_COLORS.steady;

    // Point-format ({x, y} objects) rather than a shared `labels` array,
    // because the flow series and the rainfall series don't necessarily
    // share the same timestamps — Chart.js's time scale plots each
    // dataset's own points independently against the shared x-axis.
    const flowDataset = {
        type: "line",
        label: unit,
        data: data.series.map((p) => ({ x: new Date(p.time), y: p.value })),
        borderColor: colors.line,
        backgroundColor: colors.fill,
        fill: true,
        pointRadius: 0,
        tension: 0.25,
        borderWidth: 2,
        yAxisID: "y",
        order: 1, // drawn after (on top of) the rainfall bars below
    };

    const datasets = [flowDataset];
    const scales = {
        x: { type: "time", time: { unit: "hour" }, ...AXIS_STYLE },
        y: { ...AXIS_STYLE, position: "left" },
    };

    if (hasRain) {
        datasets.unshift({
            type: "bar",
            label: `rainfall (${data.rainfall.unit})`,
            data: data.rainfall.series.map((p) => ({ x: new Date(p.time), y: p.value })),
            backgroundColor: RAIN_COLOR,
            yAxisID: "y1",
            order: 2, // drawn first, so the flow line sits visually on top
        });
        scales.y1 = {
            ...AXIS_STYLE,
            position: "right",
            grid: { display: false }, // avoid a second overlapping gridline set
        };
    }

    const ctx = body.querySelector("canvas").getContext("2d");
    new Chart(ctx, {
        data: { datasets },
        options: {
            responsive: true,
            plugins: { legend: { display: false }, tooltip: TOOLTIP_STYLE },
            scales,
        },
    });

    // data.key is set by loadSite() below before this function is called;
    // the card.id fallback only matters if renderLoaded() is ever called
    // some other way in the future.
    loadDailyChart(card, data.key || card.id.replace("card-", ""), colors);
}

/**
 * Fetch and draw the 30-day min/max/avg chart for one card. Separate
 * from renderLoaded() because it's a second, independent API call — the
 * 48-hour chart can be ready long before this one resolves. (Rainfall
 * isn't overlaid here — only on the 48-hour chart — since a meaningful
 * 30-day rain+flow comparison would need its own daily-rollup treatment,
 * left as a future improvement.)
 */
async function loadDailyChart(card, siteKey, colors) {
    const container = card.querySelector(".long-term");
    if (!container) return;

    let daily;
    try {
        const resp = await fetchJSON(`${API_BASE}/api/sites/${siteKey}/daily?days=${DAILY_LOOKBACK_DAYS}`);
        daily = resp.days || [];
    } catch (err) {
        container.innerHTML = `<p class="no-history">Couldn't load history: ${err.message}</p>`;
        return;
    }

    if (daily.length < MIN_DAYS_FOR_DAILY_CHART) {
        container.innerHTML = `<p class="no-history">Not enough history yet, check back in a few days as the poller collects more.</p>`;
        return;
    }

    // Three datasets sharing one color: a solid "avg" line, plus "max" and
    // "min" drawn transparent with fill between them — that's what creates
    // the shaded min/max band behind the average line.
    const canvas = container.querySelector("canvas");
    const ctx = canvas.getContext("2d");
    new Chart(ctx, {
        type: "line",
        data: {
            labels: daily.map((d) => d.date),
            datasets: [
                {
                    label: "avg",
                    data: daily.map((d) => d.avg),
                    borderColor: colors.line,
                    backgroundColor: "transparent",
                    pointRadius: 0,
                    tension: 0.25,
                    borderWidth: 2,
                },
                {
                    label: "max",
                    data: daily.map((d) => d.max),
                    borderColor: "transparent",
                    backgroundColor: colors.fill,
                    fill: "+1", // fill down to the next dataset (min), creating the band
                    pointRadius: 0,
                    tension: 0.25,
                },
                {
                    label: "min",
                    data: daily.map((d) => d.min),
                    borderColor: "transparent",
                    backgroundColor: colors.fill,
                    fill: false,
                    pointRadius: 0,
                    tension: 0.25,
                },
            ],
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false }, tooltip: TOOLTIP_STYLE },
            scales: {
                x: { type: "time", time: { unit: "week" }, ...AXIS_STYLE },
                y: { ...AXIS_STYLE },
            },
        },
    });
}

/** Replace a card's body with an error message, e.g. when the backend is
 * unreachable or the site has no stored data yet. */
function renderError(card, message) {
    card.classList.remove("trend-rising", "trend-falling", "trend-steady");
    const body = card.querySelector(".body");
    body.innerHTML = `<p class="error">Couldn't load data: ${message}</p>`;
}

// -----------------------------------------------------------------------
// Data loading
// -----------------------------------------------------------------------

/** Fetch one site's data and render it into its (already-existing) card. */
async function loadSite(site) {
    const card = document.getElementById(`card-${site.key}`);
    try {
        const data = await fetchJSON(`${API_BASE}/api/sites/${site.key}/data`);
        data.key = site.key; // stash it so renderLoaded() can pass it to loadDailyChart()
        renderLoaded(card, data);
    } catch (err) {
        renderError(card, err.message);
    }
}

// -----------------------------------------------------------------------
// Entry point
// -----------------------------------------------------------------------

async function main() {
    let sites;
    try {
        sites = await fetchJSON(`${API_BASE}/api/sites`);
    } catch (err) {
        sitesEl.innerHTML = `<p class="error">Couldn't reach the API at ${API_BASE}. Is the backend running? (${err.message})</p>`;
        return;
    }

    // Build all card shells first (shows the loading skeleton immediately),
    // then fire off each site's data fetch independently — one slow or
    // failing site doesn't block the others from loading.
    sites.forEach((site) => sitesEl.appendChild(makeCard(site)));
    sites.forEach(loadSite);

    // Reuses this same fetched array — no second request just for the map.
    // initMap comes from map-init.js; guarded there in case that script
    // didn't load for some reason.
    if (typeof initMap === "function") initMap(sites);

    // Keep the dashboard current without a manual refresh.
    setInterval(() => sites.forEach(loadSite), 5 * 60 * 1000);
}

main();
