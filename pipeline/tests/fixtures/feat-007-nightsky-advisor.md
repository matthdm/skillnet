# FEAT-007: California Night Sky Advisor

## Description

Build a complete REST API service that helps users find the best times and locations in
California to view the night sky, the Milky Way, and upcoming astronomical events.

The service combines four data layers into a single **viewing score** (0–100) per location
per night:

1. **Sky darkness** — hardcoded Bortle scale / SQM ratings for known California dark sky
   sites. Augmented with light pollution SQM data fetched from
   https://www.darkskymap.com/nightSkyBrightness (JSON endpoint returns `sqm` values by
   lat/lon bounding box).

2. **Cloud coverage and atmospheric seeing** — fetched from Open-Meteo
   (https://api.open-meteo.com, no API key required). Fields used:
   `cloud_cover`, `visibility`, `precipitation_probability`, `wind_speed_10m`.
   Seeing quality is derived from `wind_speed_10m` + `surface_pressure` delta.

3. **Moon phase and illumination** — calculated locally using the `ephem` library.
   Full moon illumination heavily penalises the score. New moon gives a bonus.
   Moon rise/set times for each location are computed to find dark-sky windows within
   the night.

4. **Astronomical events** — calculated locally via `ephem` and `skyfield` for the
   next 30 days: Milky Way galactic core visibility windows (seasonal, based on
   galactic center altitude at each site), meteor shower peaks (hardcoded annual
   schedule with peak ZHR), visible planet conjunctions and oppositions, and ISS
   passes (fetched from https://api.wheretheiss.at/v1/satellites/25544/positions or
   similar public TLE-based endpoint).

The service exposes a REST API and a pre-rendered interactive HTML map (using Folium)
showing all California sites colour-coded by tonight's composite score. The map is
regenerated on demand or on a schedule.

The service is localised to California only. All 20 curated sites are hardcoded with
coordinates and baseline Bortle ratings; live data layers are fetched on top.

### Curated California Sites (seed data — hardcoded in `data/sites.py`)

| id | name | lat | lon | bortle | notes |
|----|------|-----|-----|--------|-------|
| CA-01 | Death Valley — Mesquite Flat | 36.607 | -117.021 | 2 | Gold-tier IDA park |
| CA-02 | Anza-Borrego Desert | 33.257 | -116.388 | 2 | Southern CA's best |
| CA-03 | Carrizo Plain National Monument | 35.135 | -119.723 | 2 | Milky Way corridor |
| CA-04 | Joshua Tree — Skull Rock | 33.996 | -116.058 | 3 | IDA dark sky park |
| CA-05 | Lassen Volcanic NP | 40.490 | -121.508 | 2 | Northern CA anchor |
| CA-06 | Eastern Sierra — Bishop | 37.363 | -118.395 | 3 | High altitude, dry air |
| CA-07 | Sequoia — Mineral King | 36.449 | -118.596 | 3 | 7,800 ft elevation |
| CA-08 | Pinnacles National Park | 36.492 | -121.198 | 4 | Central Coast access |
| CA-09 | Mount Shasta — Black Butte | 41.330 | -122.322 | 2 | Far north isolation |
| CA-10 | Palomar Mountain | 33.357 | -116.863 | 4 | Hale Telescope site |
| CA-11 | Big Sur — Nacimiento Summit | 35.766 | -121.222 | 3 | Coastal mountain |
| CA-12 | Mojave National Preserve | 35.021 | -115.540 | 2 | Extensive dark corridor |
| CA-13 | Kings Canyon — Road's End | 36.788 | -118.577 | 2 | No light pollution N/E/W |
| CA-14 | Yosemite — Glacier Point | 37.730 | -119.573 | 4 | 7,214 ft, accessible |
| CA-15 | Mount Tamalpais — East Peak | 37.924 | -122.599 | 5 | Best near Bay Area |
| CA-16 | Tehachapi Mountains | 35.130 | -118.452 | 3 | Southern Sierra access |
| CA-17 | San Jacinto Peak | 33.815 | -116.679 | 3 | Tram-accessible, 10,834 ft |
| CA-18 | Point Reyes National Seashore | 38.083 | -122.869 | 4 | Coastal, marine layer risk |
| CA-19 | Lake Almanor | 40.230 | -121.127 | 3 | Northern interior |
| CA-20 | Crater Lake — Glass Mountain | 41.551 | -121.513 | 2 | Remote, no facilities |

---

## Acceptance Criteria

### Data layer — Sky darkness
- `GET /sites` returns all 20 sites with baseline `bortle` and `sqm_baseline` values.
- On startup and every 6 hours, the service attempts to enrich SQM values by fetching
  from `https://www.darkskymap.com/nightSkyBrightness` for each site's bounding box
  (`lat±0.1, lon±0.1`). If the fetch fails or returns no data, the hardcoded baseline
  is used. Enrichment errors are logged but do not crash the service.
- Each site record includes `sqm` (float, higher = darker, 22.0 = Bortle 1), `bortle`
  (int 1–9), and `darkness_score` (0–100 linear map from Bortle 9→0 to Bortle 1→100).

### Data layer — Weather
- `GET /sites/{site_id}/weather` returns current and next-48h forecasts from Open-Meteo
  for that site's coordinates. Fields: `cloud_cover` (%), `visibility` (m),
  `precipitation_probability` (%), `wind_speed_10m` (km/h), `weather_code` (WMO).
- `cloud_score` = `max(0, 100 - cloud_cover)`. `seeing_score` derived from wind speed:
  ≤10 km/h → 100, 10–30 → linear decay to 50, >30 → 0.
- Weather is cached per site for 1 hour. Cache miss triggers a live fetch.

### Data layer — Moon
- `GET /moon` returns current phase name, illumination percent (0.0–1.0), rise time,
  set time, and next new/full moon dates (all in UTC and US/Pacific).
- `moon_score` = `round((1 - illumination) * 100)`. Applied uniformly to all sites.
- Each site's nightly forecast includes a `dark_window` — the contiguous period between
  astronomical dusk and astronomical dawn during which the moon is below the horizon.
  Computed via `ephem` observer set to site coordinates.
- Milky Way galactic core (`RA 17h 45m, Dec -29°`) must be above 20° altitude during
  the dark window for `milky_way_visible: true`. Altitude computed via `ephem`.

### Data layer — Astronomical events
- `GET /events` returns upcoming events for the next 30 days, sorted by date. Each
  event has: `type`, `name`, `date_utc`, `description`, `peak_zhr` (meteors only),
  `magnitude` (planets only), `best_viewing_time_utc`, `visible_from_california: bool`.
- Event types included:
  - **Meteor showers** — hardcoded annual schedule (Perseids, Geminids, Leonids,
    Quadrantids, Eta Aquarids, Orionids, Lyrids, Delta Aquarids) with peak date ± 2 days
    window, ZHR, and optimal viewing window (typically 1–4 AM local).
  - **Planet oppositions/conjunctions** — calculated for the next 30 days via `ephem`
    for Mars, Jupiter, Saturn, Venus, Mercury. Opposition = elongation > 160°,
    conjunction = elongation < 10°.
  - **ISS passes** — fetched from a public TLE source (Celestrak or wheretheiss.at).
    For each California site, compute passes with max elevation > 30° using `ephem`
    for the next 7 days. Minimum 60-second visible duration.
  - **Moon phases** — next new moon and full moon dates always included.
- Events are recalculated every 24 hours. Calculation errors for any event type are
  caught, logged, and skipped — other events still return.

### Composite score
- `GET /sites/{site_id}/score?date=YYYY-MM-DD` returns the composite viewing score for
  that site on the given date (defaults to tonight if omitted).
- Score formula:
  ```
  composite = (
      darkness_score * 0.30 +
      cloud_score    * 0.35 +
      moon_score     * 0.20 +
      seeing_score   * 0.15
  )
  ```
  Clamped to 0–100, rounded to one decimal place.
- Response includes all component scores, `milky_way_visible`, `dark_window_start`,
  `dark_window_end`, and a list of `active_events` for that date (from `/events`).
- `GET /sites/scores?date=YYYY-MM-DD` returns composite scores for all 20 sites,
  sorted descending by composite score.

### Map
- `GET /map` returns a self-contained HTML page (Folium map, no external dependencies).
- Map centred on California (lat 37.5, lon -119.5), zoom 6.
- Each site rendered as a circle marker. Colour coding by composite score:
  - ≥80: dark green
  - 60–79: light green
  - 40–59: yellow
  - 20–39: orange
  - <20: red
- Marker popup shows: site name, composite score, cloud cover %, moon illumination %,
  `milky_way_visible` flag, and any active events tonight.
- A legend in the top-right corner shows the colour bands.
- Map data is for tonight by default. `GET /map?date=YYYY-MM-DD` renders for a specific date.
- Map is regenerated on each request (Folium rendering is fast enough; no caching needed).

### API general
- `GET /health` returns `{"status": "ok", "sites": 20, "cache_age_seconds": N}`.
- All timestamps in responses are ISO 8601 UTC with US/Pacific offset also provided.
- All endpoints return JSON (except `/map` which returns `text/html`).
- Invalid `site_id` returns 404. Invalid `date` format returns 422.
- The service exposes port 8090.
- A `Dockerfile` builds and runs the service:
  `CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]`
- A `config.py` module with `Settings` (Pydantic BaseSettings):
  - `port: int = 8090`
  - `cache_ttl_weather_seconds: int = 3600`
  - `cache_ttl_events_seconds: int = 86400`
  - `darkskymap_url: str = "https://www.darkskymap.com/nightSkyBrightness"`
  - `open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"`
  - `timezone: str = "America/Los_Angeles"`
- `README.md` documents: build instructions, run instructions, all endpoints with
  example responses, data sources used, and scoring formula.

### Tests
- Unit tests for the scoring formula (known inputs → known output).
- Unit tests for moon score calculation (full moon → 0, new moon → 100).
- Unit tests for dark window calculation (mock `ephem` observer).
- Unit tests for event type `meteor_shower`: correct peak dates for Perseids (Aug 11–13)
  and Geminids (Dec 13–14).
- Unit tests for map endpoint: returns 200, content-type is `text/html`, contains
  "California Night Sky" in body.
- Integration smoke test for `/health` returns 200 with `sites: 20`.
- All external HTTP calls (Open-Meteo, darkskymap) must be mocked in tests using
  `responses` or `httpretty`. No live network calls in the test suite.

## Tech Stack

- Python
- FastAPI
- Pydantic
- uvicorn
- ephem
- skyfield
- folium
- httpx
- open-meteo (no key required)
- beautifulsoup4
- pytz
- threading

## Repo Name

nightsky-advisor
