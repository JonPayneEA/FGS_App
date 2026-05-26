# =============================================================================
# server.py
# Reactive server logic for the FGS dashboard.
#
# Responsibility: respond to user inputs, fetch fresh data, render outputs.
# Each query in data.py is wrapped in a @reactive.calc so it only re-runs
# when its specific inputs change. The map is rebuilt on each render rather
# than mutated in place, which is cheap for the size of geometry involved
# and avoids state drift between session and widget.
# =============================================================================

import ipyleaflet as L
from ipyleaflet import GeoData, Map, basemaps
from shiny import reactive, render
from shinywidgets import render_widget

import data

# FGS colour scheme from the User Guide. Used for both polygon fill and
# risk-bar swatches in the right-hand panel.
RISK_COLOUR_HEX = {
    "Red":    "#b02020",
    "Amber":  "#e08020",
    "Yellow": "#f0d040",
    "Green":  "#4f9d4f",
}


def server(input, output, session):

    # -------------------------------------------------------------------------
    # REACTIVE: latest statement metadata
    # Refreshed on every render of the app so a new FGS is picked up without
    # needing to reload the page. Cheap query, single row.
    # -------------------------------------------------------------------------
    @reactive.calc
    def latest_statement():
        return data.fetch_latest_statement()

    # -------------------------------------------------------------------------
    # REACTIVE: filter inputs collected into a single object
    # Holds (statement_id, day_index, selected sources, selected matrix cells,
    # intersection threshold). Downstream reactives depend on this so they
    # only re-run when an input that actually matters changes.
    # -------------------------------------------------------------------------
    @reactive.calc
    def filters():
        stmt = latest_statement()
        if not stmt:
            return None

        sources = [s for s, on in {
            "river":   input.src_river(),
            "surface": input.src_surface(),
            "coastal": input.src_coastal(),
            "ground":  input.src_ground(),
        }.items() if on]

        # Matrix cells: any checkbox with id mtx_{x}_{y} that's ticked.
        matrix_cells = []
        for x in range(1, 5):
            for y in range(1, 5):
                if input[f"mtx_{x}_{y}"]():
                    matrix_cells.append((x, y))

        return {
            "statement_id":     int(stmt["statement_id"]),
            "day_index":        int(input.day_index()),
            "sources":          sources,
            "matrix_cells":     matrix_cells,
            "min_intersection": input.intersect_threshold() / 100.0,
        }

    # -------------------------------------------------------------------------
    # REACTIVE: polygons for the current statement + day
    # Filtered client-side in pandas after the query. Cheaper than rewriting
    # the SQL with a dynamic IN clause and easier to debug.
    # -------------------------------------------------------------------------
    @reactive.calc
    def polygons():
        f = filters()
        if not f:
            return None

        gdf = data.fetch_risk_polygons(f["statement_id"], f["day_index"])
        if gdf.empty:
            return gdf

        # Source filter.
        gdf = gdf[gdf["source"].isin(f["sources"])]

        # Risk matrix filter: keep polygons whose (risk_x, risk_y) is selected.
        keep_pairs = set(f["matrix_cells"])
        gdf = gdf[gdf.apply(lambda r: (r["risk_x"], r["risk_y"]) in keep_pairs, axis=1)]

        return gdf

    # -------------------------------------------------------------------------
    # REACTIVE: EA areas affected by the current statement + day
    # -------------------------------------------------------------------------
    @reactive.calc
    def ea_areas():
        f = filters()
        if not f:
            return None
        df = data.fetch_ea_areas_for_statement(f["statement_id"], f["day_index"])
        df = df[df["source"].isin(f["sources"])]
        df = df[df["intersection_pct"] >= f["min_intersection"]]
        return df

    # -------------------------------------------------------------------------
    # REACTIVE: headline counts
    # -------------------------------------------------------------------------
    @reactive.calc
    def counts():
        f = filters()
        if not f:
            return {"polygon_count": 0, "ea_area_count": 0, "constituency_count": 0}
        return data.fetch_summary_counts(f["statement_id"], f["day_index"])

    # -------------------------------------------------------------------------
    # OUTPUT: title and subtitle
    # -------------------------------------------------------------------------
    @output
    @render.text
    def map_title():
        stmt = latest_statement()
        if not stmt:
            return "No statement loaded"
        day = int(input.day_index())
        return f"Forecast Day {day} · issued {stmt['issued_at']:%d %b %Y %H:%M}"

    @output
    @render.text
    def map_subtitle():
        c = counts()
        return f"Statement {latest_statement().get('statement_id', '–')} · " \
               f"{c['polygon_count']} polygons · {c['ea_area_count']} EA areas"

    # -------------------------------------------------------------------------
    # OUTPUT: headline count cards
    # -------------------------------------------------------------------------
    @output
    @render.text
    def count_polygons():
        return str(counts()["polygon_count"])

    @output
    @render.text
    def count_ea_areas():
        return str(counts()["ea_area_count"])

    @output
    @render.text
    def count_constituencies():
        return str(counts()["constituency_count"])

    # -------------------------------------------------------------------------
    # OUTPUT: the EA area list in the right panel
    # Built as HTML rather than a Shiny DataGrid because the visual format
    # (coloured risk bar, two-line label) doesn't fit a tabular widget.
    # -------------------------------------------------------------------------
    @output
    @render.ui
    def ea_area_list():
        from shiny import ui as _ui

        df = ea_areas()
        if df is None or df.empty:
            return _ui.tags.p("No EA areas above threshold.", class_="text-muted small")

        rows = []
        for _, r in df.head(20).iterrows():
            colour = r["risk_colour"]
            rows.append(
                _ui.div(
                    _ui.div(class_=f"risk-bar {colour}"),
                    _ui.div(
                        _ui.tags.strong(r["ea_area_name"]),
                        _ui.tags.br(),
                        _ui.tags.small(
                            f"{r['ea_area_type']} · {r['source']} · "
                            f"{r['intersection_pct']*100:.0f}%",
                            class_="text-muted",
                        ),
                    ),
                    class_="area-row",
                )
            )

        if len(df) > 20:
            rows.append(
                _ui.tags.small(
                    f"+ {len(df) - 20} more",
                    class_="text-muted d-block text-center mt-2",
                )
            )
        return _ui.tags.div(*rows)

    # -------------------------------------------------------------------------
    # OUTPUT: the Leaflet map
    # ipyleaflet renders inside an output_widget. Rebuilt on each render of
    # the reactive sources rather than mutated, because:
    #   - ipyleaflet's add/remove APIs are awkward for bulk layer swaps
    #   - the data volumes are small (tens of polygons, hundreds of points)
    #   - clean rebuild gives a predictable state every time
    # -------------------------------------------------------------------------
    @output
    @render_widget
    def live_map():
        m = Map(
            basemap=basemaps.OpenStreetMap.Mapnik,
            center=(53.2, -2.0),
            zoom=6,
            scroll_wheel_zoom=True,
        )

        gdf = polygons()
        if gdf is not None and not gdf.empty and input.layer_polygons():
            # Use GeoData with a style callback driven by risk_colour.
            geo = GeoData(
                geo_dataframe=gdf,
                style_callback=lambda feat: {
                    "color":     RISK_COLOUR_HEX[feat["properties"]["risk_colour"]],
                    "fillColor": RISK_COLOUR_HEX[feat["properties"]["risk_colour"]],
                    "weight":    1.5,
                    "fillOpacity": 0.45,
                },
                hover_style={"weight": 3, "fillOpacity": 0.65},
                name="FGS polygons",
            )
            m.add(geo)

        # EA flood area markers added as circle markers at each centroid.
        # Full polygon geometries available if you'd rather show shapes; we
        # use points to keep the map readable when many areas are affected.
        if input.layer_ea_areas():
            df = ea_areas()
            if df is not None and not df.empty:
                # The intersection table doesn't store geometry. To plot
                # markers we'd need to join to ea_flood_warning_areas /
                # ea_flood_alert_areas. For the first pass we skip and
                # display them in the right-hand list only. Extending this
                # is the obvious next step.
                pass

        return m
