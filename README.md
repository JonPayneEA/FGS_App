# FGS Flood Guidance Dashboard

Shiny for Python application reading from Databricks Delta tables, designed
for deployment to Posit Connect. This first version covers only the Live
View tab; analytics tabs are placeholders.

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

Set the same five environment variables in the Posit Connect web UI under
the deployed app's *Vars* tab. The `.env` file is for local dev only and
should not be deployed (add it to a `.rscignore` if needed).

## Data dependencies

Reads from four Delta tables in the configured catalog/schema:

- `fgs_statements` -- one row per FGS issue
- `fgs_risk_polygons` -- one row per polygon, with the six risk matrix
  columns: `risk_x`, `risk_y`, `impact_label`, `likelihood_label`,
  `risk_level`, `risk_colour`
- `fgs_ea_area_intersections` -- polygons intersected with EA flood areas
- `fgs_constituency_intersections` -- polygons intersected with constituencies

Assumes the intersection tables carry through the risk matrix columns from
the polygon table. If your intersection tables still use the older
`risk_level_min`/`risk_level_max` schema, the queries in `data.py` will need
adjustment to derive `risk_colour` and `risk_level` on the fly.

## Known gaps for v1

- EA flood area markers are listed in the right panel but not yet plotted on
  the map. Requires joining the intersection table to `ea_flood_warning_areas`
  / `ea_flood_alert_areas` for geometry. Straightforward extension.
- Constituency layer toggle has no rendering yet, same reason.
- Tile basemap uses public OpenStreetMap. If the EA proxy blocks it in
  production, swap `basemaps.OpenStreetMap.Mapnik` for an internal tile URL.
- No query caching. Every reactive recompute hits Databricks. For the live
  view this is fine (data is small and the user expects fresh state); for
  the analytics tabs added later, wrap calls in `@reactive.calc` with
  `lru_cache` or use Shiny's session-level cache.
