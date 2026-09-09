// =============================================================================
// Palmy River Watch — map initialization
//
// Renders a real Leaflet + OpenStreetMap map of the monitored gauges,
// driven entirely by the /api/sites response (see app.js's main()) rather
// than any hand-computed coordinates. If a site's lat/lon in
// backend/sites.py ever changes, this map is correct again the next time
// the page loads — there's nothing to manually regenerate, unlike the
// static SVG this replaced.
//
// Note: unlike this app's other dependencies (Chart.js, Leaflet itself),
// map *tiles* can't be vendored locally — they're fetched live from
// OpenStreetMap's tile servers each time the map renders. This is the
// one deliberate exception to the project's "no CDN" rule, because
// there isn't really an alternative for a real, pannable map.
// =============================================================================

// Not a monitored gauge — just a landmark so the map has a familiar
// reference point. Hand-set here rather than coming from the API, since
// it's fixed and isn't part of the data model in sites.py.
const PALMY_CBD = { lat: -40.3557, lon: 175.6094, label: "Palmerston North CBD" };

const SITE_MARKER_COLOR = "#1C7C93"; // --river — matches the site favicon's water motif
const CBD_MARKER_COLOR = "#1D3461"; // --navy — the app's brand/landmark color

/**
 * Build the map inside #map and drop a marker for the CBD landmark plus
 * every site passed in. Called once from app.js's main(), reusing the
 * same /api/sites response the cards are built from (no second fetch).
 */
function initMap(sites) {
  const container = document.getElementById("map");
  if (!container || typeof L === "undefined") return;

  const map = L.map(container, {
    // Scrolling the page shouldn't get hijacked just because the cursor
    // happens to pass over the map.
    scrollWheelZoom: false,
  }).setView([PALMY_CBD.lat, PALMY_CBD.lon], 12);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 18,
  }).addTo(map);

  L.circleMarker([PALMY_CBD.lat, PALMY_CBD.lon], {
    radius: 7,
    color: "#FFFFFF",
    weight: 2,
    fillColor: CBD_MARKER_COLOR,
    fillOpacity: 1,
  })
    .addTo(map)
    .bindTooltip(PALMY_CBD.label, { direction: "top" });

  // Collect every marker's position so we can fit the view to them all,
  // rather than trusting a hardcoded zoom level that might crop a site
  // off-screen if its coordinates ever change.
  const bounds = [[PALMY_CBD.lat, PALMY_CBD.lon]];

  sites.forEach((site) => {
    if (typeof site.lat !== "number" || typeof site.lon !== "number") return;
    bounds.push([site.lat, site.lon]);

    L.circleMarker([site.lat, site.lon], {
      radius: 8,
      color: "#FFFFFF",
      weight: 2,
      fillColor: SITE_MARKER_COLOR,
      fillOpacity: 0.9,
    })
      .addTo(map)
      .bindPopup(`<strong>${site.label}</strong><br>${site.description}`)
      .bindTooltip(site.label, { direction: "top" });
  });

  if (bounds.length > 1) {
    map.fitBounds(bounds, { padding: [30, 30] });
  }
}
