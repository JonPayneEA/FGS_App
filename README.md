# FGS Flood Guidance Dashboard

Shiny for Python application reading from Databricks Delta tables, designed
for deployment to Posit Connect. It is read-only. The **Today** tab is built
for a senior forecaster: a ranked constituency table (forecast risk, five-day
outlook, escalations, warnings in force, the MP) beside a linked map, under a
pipeline health strip. Later phases add What changed, Briefings, Exposure
history and Pipeline health tabs.

## Files

| File | Purpose |
|---|---|
| `app.py` | Entry point. Wires UI and server together. |
| `ui.py` | Declarative layout, sidebar, panels, custom CSS. |
| `server.py` | Reactive logic, map rendering, output callbacks. |
| `data.py` | All SQL queries against the Delta tables. |
| `requirements.txt` | Python dependencies. |
| `.env.example` | Template for connection credentials. |

## Setup

1. Copy `.env.example` to `.env` and fill in your Databricks SQL warehouse
   details. The token needs read access to the FGS catalog/schema.

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Run locally:
   ```
   shiny run --reload app.py
   ```
   The app opens at http://localhost:8000.

## Deployment to Posit Connect

Recommended via `rsconnect-python`:

```
pip install rsconnect-python
rsconnect deploy shiny --name <connect_server_name> .
```

Set the same environment variables (see `.env.example`) in the Posit Connect web UI under
the deployed app's *Vars* tab. The `.env` file is for local dev only and
should not be deployed (add it to a `.rscignore` if needed).

## Data dependencies

All tables are in the unrestricted schema (default
`prd_dash_lab.flood_forecasting_unrestricted`). The app token needs `SELECT`
on that schema only and must not be granted the restricted schema.

| Table | Used for |
|---|---|
| `fgs_statements`, `fgs_intersections_log` | Choosing the current statement |
| `constituency_flood_situation` | The Today table and headline cards (pipeline job 19) |
| `parliamentary_constituencies` | Boundaries for the map (GeoJSON string, EPSG:4326) |
| `flood_warnings_current`, `ea_flood_warning_areas`, `ea_flood_alert_areas` | Warning and alert areas in force |
| `fgs_risk_polygons` | Optional FGS polygon layer |
| `fgs_ea_area_intersections`, `fgs_constituency_intersections` | EA area list for the forecast day |
| `pipeline_health` | The health strip (job 22) |

### Rules the app shares with the pipeline

- **Current statement.** The latest statement whose row in
  `fgs_intersections_log` is up to date for the configured geometry source,
  the same rule as pipeline jobs 14 and 18. If a newer statement exists but is
  still being processed, a banner says so and the previous one stays on screen.
- **Geometry source.** Set `FGS_GEOMETRY_SOURCE` to the value in the
  pipeline's `config.py` (`risk_areas` or `aoc`). Every intersection query is
  filtered to it, so the two sources are never counted together. The optional
  FGS polygon layer reads `fgs_risk_polygons`, which is always the risk area
  set.
- **Caching.** Query results are cached for `FGS_CACHE_SECONDS` (default 90)
  and shared across sessions.

## Phasing

1. Today tab, data layer, health strip (this version).
2. What changed (`fgs_constituency_changes`), then Briefings
   (`mp_briefing_drafts`, read-only, restricted to a Posit Connect group).
   Needs a service principal with `SELECT` only.
3. Exposure history (`fgs_constituency_exposure`) and a full Pipeline health tab.

Observed gauge readings and thresholds are out of scope for now. Thresholds
sit in the restricted schema, which the app does not read.

## Known gaps

- The map relies on the area and constituency tables holding WGS84 GeoJSON
  strings in a `geometry` column. If the tables change CRS, reproject in
  `data.py`.
- Selecting a constituency only moves the map for constituencies that are
  drawn (those with a forecast risk or a warning).
- Tile basemap uses public OpenStreetMap. If the EA proxy blocks it in
  production, swap `basemaps.OpenStreetMap.Mapnik` for an internal tile URL.
