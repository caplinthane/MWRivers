"""
Monitored sites for Palmy River Watch.

Site and measurement names must match EXACTLY what Horizons Regional
Council's Hilltop Server reports. If Horizons renames a site or
measurement, update it here.

To discover current site/measurement names yourself, query:
  https://hilltopserver.horizons.govt.nz/data.hts?Service=Hilltop&Request=SiteList
  https://hilltopserver.horizons.govt.nz/data.hts?Service=Hilltop&Request=MeasurementList&Site=<site name>

...or, once the backend is running with an admin key set up, hit
GET /api/debug/raw/{key} to see Horizons' raw response for a site that's
already configured here.

What each field is for:
  label               - shown in the UI as the site's display name
  description         - the one-line blurb under the label on each card
  site_name           - must match Hilltop's SiteList exactly (see above)
  measurement         - must match Hilltop's MeasurementList for that site
  lat, lon            - real coordinates, used by frontend/map-init.js to
                        place markers on the live Leaflet map
  watch_level         - reserved for a future "flag when level exceeds X"
                        feature; intentionally left unset, see below
  rainfall_site        - optional: a nearby Hilltop rainfall station, so
                        the frontend can overlay rain against flow
  rainfall_measurement - the exact measurement name at that rainfall site
  rainfall_key          - a short id for that rainfall station, used as its
                        own row in the database. Two river sites that
                        share a rainfall station (Turitea and Manawatu
                        both use Ngahere Park) use the same rainfall_key,
                        so poller.py only fetches and stores it once per
                        poll instead of twice.
  rainfall_co_located    - True if rainfall_site is genuinely at the same
                        spot as the river gauge, False if it's a nearby
                        proxy — shown in the UI so the correlation isn't
                        overstated.

Rainfall site choices (checked against Horizons' live CollectionList,
not guessed):
  - Turitea and Kahuterawa each have a rainfall gauge in the *same*
    catchment (Ngahere Park Climate Station and Kahuterawa at Scotts
    Road respectively).
  - Manawatu at Teachers College has no rainfall gauge at that exact
    spot, so it reuses Ngahere Park Climate Station as the nearest
    representative station for general Palmerston North rainfall —
    it's a proxy, not a perfect catchment match, which is why the
    frontend labels it "nearby" rather than implying it's on-site.
"""

# key -> config used by the API and frontend
SITES = {
    "manawatu-teachers-college": {
        "label": "Manawatū River at Teachers College",
        "description": "Main river gauge in central Palmerston North.",
        "site_name": "Manawatu at Teachers College",
        "measurement": "Flow [Water Level]",
        "lat": -40.3523,
        "lon": 175.6119,
        # Rough indicative levels only — NOT official flood thresholds.
        # Always defer to Horizons Regional Council for real flood risk info.
        "watch_level": None,
        # Nearest available rainfall station, not co-located — see note above.
        "rainfall_site": "Ngahere Park Climate Station",
        "rainfall_measurement": "Rainfall [SCADA Rainfall]",
        "rainfall_key": "ngahere-park-climate-station",
        "rainfall_co_located": False,
    },
    "turitea-ngahere-park": {
        "label": "Turitea Stream at Ngahere Park",
        "description": "Stream through the eastern Palmerston North suburbs.",
        "site_name": "Turitea at Ngahere Park",
        "measurement": "Flow [Water Level]",
        "lat": -40.3778,
        "lon": 175.6486,
        "watch_level": None,
        # Same location as the flow gauge — a real co-located match.
        "rainfall_site": "Ngahere Park Climate Station",
        "rainfall_measurement": "Rainfall [SCADA Rainfall]",
        "rainfall_key": "ngahere-park-climate-station",
        "rainfall_co_located": True,
    },
    "kahuterawa-johnstons-rata": {
        "label": "Kahuterawa Stream at Johnstons Rata",
        "description": "Stream draining the Tararua foothills SW of the city.",
        "site_name": "Kahuterawa at Johnstons Rata",
        "measurement": "Flow [Water Level]",
        "lat": -40.4116,
        "lon": 175.5622,
        "watch_level": None,
        # Same catchment, a few km upstream — this site only exposes moving
        # totals (not an instantaneous rate), unlike the other two.
        "rainfall_site": "Kahuterawa at Scotts Road",
        "rainfall_measurement": "Moving Total (24 Hours) [SCADA Rainfall]",
        "rainfall_key": "kahuterawa-at-scotts-road",
        "rainfall_co_located": True,
    },
}
