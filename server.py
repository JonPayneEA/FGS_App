# =============================================================================
# server.py
# Reactive server logic for the FGS dashboard.
#
# Responsibility: respond to user inputs, fetch fresh data, render outputs.
# Each query in data.py is wrapped in a @reactive.calc so it only re-runs
# when its specific inputs change (data.py also holds a short shared cache).
# The map is rebuilt on each render rather than mutated in place, which is
# cheap for the size of geometry involved and avoids state drift between
# session and widget. The one exception is zoom-to-selection, which moves the
# existing widget so choosing a table row does not redraw every layer.
# =============================================================================

import json

import pandas as pd
from ipyleaflet import GeoJSON, Map, basemaps
from shiny import reactive, render, ui
from shinywidgets import render_widget

import data

# FGS colour scheme from the User Guide. Used for polygon fill, risk-bar
# swatches and the constituency choropleth.
RISK_COLOUR_HEX = {
    "Red":    "#b02020",
    "Amber":  "#e08020",
    "Yellow": "#f0d040",
    "Green":  "#4f9d4f",
}

# Pipeline risk ranks: 1 Very Low (Green), 2 Low (Yellow), 3 Medium (Amber),
# 4 High (Red), 0 nothing flagged.
RANK_LABEL = {0: "–", 1: "Very Low", 2: "Low", 3: "Medium", 4: "High"}
RANK_SHORT = {0: "–", 1: "VL", 2: "L", 3: "M", 4: "H"}
RANK_HEX = {
    0: "#adb5bd",
    1: RISK_COLOUR_HEX["Green"],
    2: RISK_COLOUR_HEX["Yellow"],
    3: RISK_COLOUR_HEX["Amber"],
    4: RISK_COLOUR_HEX["Red"],
}

# Warning severity: 1 Severe Flood Warning, 2 Flood Warning, 3 Flood Alert.
SEVERITY_HEX = {1: "#7a0f0f", 2: "#d62828", 3: "#f4a259"}
SEVERITY_LABEL = {1: "Severe Flood Warning", 2: "Flood Warning", 3: "Flood Alert"}

HEALTH_ORDER = {"ok": 0, "info": 1, "warn": 2, "fail": 3}


def _rank(value) -> int:
    """A rank as a plain int; missing values count as 0 (nothing flagged)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _styled_layer(gdf, style_fn, hover_style, name):
    """A GeoJSON layer with a style written into every feature.

    ipyleaflet reads feature.properties.style on the front end. Writing it here
    avoids relying on GeoData's style_callback, which left features unstyled in
    testing. Timestamps and NaN are converted by GeoDataFrame.to_json.
    """
    collection = json.loads(gdf.to_json(default=str))
    for feature in collection["features"]:
        feature["properties"]["style"] = style_fn(feature["properties"])
    return GeoJSON(data=collection, hover_style=hover_style, name=name)


def server(input, output, session):

    # -------------------------------------------------------------------------
    # REACTIVE: the statement being shown
    # The latest statement with complete intersections for the configured
    # geometry source. 'pending' says a newer one is still being processed.
    # -------------------------------------------------------------------------
    @reactive.calc
    def current_statement():
        return data.fetch_current_statement()

    # -------------------------------------------------------------------------
    # REACTIVE: filter inputs for the map polygons and EA area list
    # -------------------------------------------------------------------------
    @reactive.calc
    def filters():
        stmt = current_statement()
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

        gdf = gdf[gdf["source"].isin(f["sources"])]
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
        # Coastal lines have no area share (NULL), so they are always kept.
        df = df[df["intersection_pct"].isna() | (df["intersection_pct"] >= f["min_intersection"])]
        return df

    # -------------------------------------------------------------------------
    # REACTIVE: the constituency situation, all 650 rows, then the filtered view
    # -------------------------------------------------------------------------
    @reactive.calc
    def situation_all():
        return data.fetch_constituency_situation()

    @reactive.calc
    def situation():
        df = situation_all()
        if df.empty:
            return df
        keep = (df["fgs_worst_rank"].fillna(0) >= int(input.min_rank())) \
            | df["has_active_warning"].fillna(False).astype(bool)
        if input.only_escalated():
            keep &= df["fgs_escalated"].fillna(False).astype(bool)
        out = df[keep].copy()
        # Worst forecast first, then escalations, then warnings in force.
        out["_warn"] = out["severe_warnings"].fillna(0) * 100 + out["warnings"].fillna(0) * 10 \
            + out["alerts"].fillna(0)
        out = out.sort_values(
            ["fgs_worst_rank", "fgs_escalated", "_warn", "constituency_name"],
            ascending=[False, False, False, True],
        ).drop(columns="_warn")
        return out

    # -------------------------------------------------------------------------
    # REACTIVE: shapes for the constituency choropleth and warning areas
    # -------------------------------------------------------------------------
    @reactive.calc
    def constituency_shapes():
        df = situation_all()
        if df.empty:
            return None
        flagged = df[(df["fgs_worst_rank"].fillna(0) >= 1)
                     | df["has_active_warning"].fillna(False).astype(bool)]
        gdf = data.fetch_constituency_geometry(tuple(sorted(flagged["constituency_id"])))
        if gdf.empty:
            return gdf
        cols = ["constituency_id", "constituency_name", "fgs_worst_rank", "fgs_worst_level",
                "mp_name", "has_active_warning"]
        gdf = gdf.merge(flagged[cols], on="constituency_id", how="left")
        gdf["has_active_warning"] = gdf["has_active_warning"].fillna(False).astype(bool)
        return gdf

    @reactive.calc
    def warning_shapes():
        return data.fetch_warning_areas_in_force()

    # -------------------------------------------------------------------------
    # OUTPUT: pipeline health strip
    # One line. The worst check decides the colour; failing or warning checks
    # are named so the forecaster knows whether to trust what is on screen.
    # -------------------------------------------------------------------------
    @output
    @render.ui
    def health_strip():
        try:
            df = data.fetch_pipeline_health()
        except Exception as exc:  # the strip must never take the page down
            return ui.div(f"Pipeline health unavailable: {type(exc).__name__}",
                          class_="health-strip warn")
        if df.empty:
            return ui.div("Pipeline health: no checks recorded", class_="health-strip warn")

        worst = max(df["status"], key=lambda s: HEALTH_ORDER.get(s, 0))
        problems = df[df["status"].isin(["warn", "fail"])]
        if problems.empty:
            text = "Pipeline healthy"
        else:
            text = "Pipeline: " + "; ".join(
                f"{r.check_name} ({r.status})" for r in problems.itertuples()
            )
        checked = pd.to_datetime(df["checked_at"], errors="coerce").max()
        stamp = f" · checked {checked:%d %b %H:%M} UTC" if pd.notna(checked) else ""
        return ui.div(text + stamp, class_=f"health-strip {worst}")

    # -------------------------------------------------------------------------
    # OUTPUT: statement banner (only when a newer statement is still processing)
    # -------------------------------------------------------------------------
    @output
    @render.ui
    def statement_banner():
        stmt = current_statement()
        if not stmt:
            return ui.div(
                "No statement has complete intersections yet. Nothing to show.",
                class_="banner",
            )
        if stmt.get("pending"):
            return ui.div(
                "A newer statement has been issued and is still being processed. "
                f"Showing statement {stmt['statement_id']}, issued "
                f"{pd.to_datetime(stmt['issued_at']):%d %b %H:%M}.",
                class_="banner",
            )
        return None

    # -------------------------------------------------------------------------
    # OUTPUT: headline cards (computed from the unfiltered situation table, so
    # they do not move when the table filters change)
    # -------------------------------------------------------------------------
    def _count(mask_fn) -> str:
        df = situation_all()
        if df.empty:
            return "–"
        return str(int(mask_fn(df).sum()))

    @output
    @render.text
    def card_medium_plus():
        return _count(lambda d: d["fgs_worst_rank"].fillna(0) >= 3)

    @output
    @render.text
    def card_escalated():
        return _count(lambda d: d["fgs_escalated"].fillna(False).astype(bool))

    @output
    @render.text
    def card_warnings():
        return _count(lambda d: (d["severe_warnings"].fillna(0) + d["warnings"].fillna(0)) > 0)

    @output
    @render.text
    def card_alerts():
        return _count(lambda d: d["alerts"].fillna(0) > 0)

    # -------------------------------------------------------------------------
    # OUTPUT: title and subtitle
    # -------------------------------------------------------------------------
    @output
    @render.text
    def map_title():
        stmt = current_statement()
        if not stmt:
            return "No statement loaded"
        return f"Statement {stmt['statement_id']} · issued {pd.to_datetime(stmt['issued_at']):%d %b %Y %H:%M}"

    @output
    @render.text
    def map_subtitle():
        df = situation()
        return f"{len(df)} constituencies shown · EA area list and map polygons are for forecast day {input.day_index()}"

    # -------------------------------------------------------------------------
    # OUTPUT: the constituency table
    # One row per constituency from constituency_flood_situation. Columns are
    # renamed for reading; the ONS code stays as the first column because the
    # selection handler needs it.
    # -------------------------------------------------------------------------
    @render.data_frame
    def situation_table():
        df = situation()
        out = pd.DataFrame({
            "Code":          df["constituency_id"],
            "Constituency":  df["constituency_name"],
            "MP":            df["mp_name"].fillna("(vacant or unmatched)"),
            "Worst":         df["fgs_worst_rank"].map(lambda r: RANK_LABEL[_rank(r)]),
            "First day":     pd.to_datetime(df["fgs_worst_date"], errors="coerce").dt.strftime("%a %d").fillna("–"),
            "Sources":       df["fgs_worst_sources"].fillna(""),
            "D1": df["fgs_rank_day1"].map(lambda r: RANK_SHORT[_rank(r)]),
            "D2": df["fgs_rank_day2"].map(lambda r: RANK_SHORT[_rank(r)]),
            "D3": df["fgs_rank_day3"].map(lambda r: RANK_SHORT[_rank(r)]),
            "D4": df["fgs_rank_day4"].map(lambda r: RANK_SHORT[_rank(r)]),
            "D5": df["fgs_rank_day5"].map(lambda r: RANK_SHORT[_rank(r)]),
            "Change":        df["fgs_escalated"].map(lambda v: "Escalated" if v else ""),
            "Warn S/W/A":    df["severe_warnings"].fillna(0).astype(int).astype(str) + "/"
                             + df["warnings"].fillna(0).astype(int).astype(str) + "/"
                             + df["alerts"].fillna(0).astype(int).astype(str),
        })
        return render.DataGrid(out, selection_mode="row", height="460px", width="100%", filters=False)

    # -------------------------------------------------------------------------
    # REACTIVE: the selected constituency's row, or None
    # data_view reflects the user's sort, so the right row is returned however
    # the table has been ordered.
    # -------------------------------------------------------------------------
    @reactive.calc
    def selected():
        view = situation_table.data_view(selected=True)
        if view is None or view.empty:
            return None
        code = view.iloc[0]["Code"]
        df = situation_all()
        row = df[df["constituency_id"] == code]
        return None if row.empty else row.iloc[0]

    # -------------------------------------------------------------------------
    # OUTPUT: detail for the selected constituency
    # -------------------------------------------------------------------------
    @output
    @render.ui
    def constituency_detail():
        r = selected()
        if r is None:
            return ui.tags.small("Select a constituency to see its MP, five-day outlook and warnings.",
                                 class_="text-muted")

        chips = [
            ui.span(f"D{d}: {RANK_LABEL[_rank(r[f'fgs_rank_day{d}'])]}",
                    class_=f"rank-chip rank-{_rank(r[f'fgs_rank_day{d}'])} me-1")
            for d in range(1, 6)
        ]
        contact = []
        if r["mp_matched"]:
            for label, key in (("Email", "mp_email"), ("Phone", "mp_phone"), ("Web", "mp_website")):
                if pd.notna(r[key]) and r[key]:
                    contact.append(ui.tags.div(f"{label}: {r[key]}", class_="small"))
        else:
            contact.append(ui.tags.div("No MP matched (vacancy, or a name mismatch).", class_="small text-muted"))

        return ui.div(
            ui.h6(r["constituency_name"], class_="panel-h"),
            ui.tags.div(
                ui.tags.strong(r["mp_name"] if r["mp_matched"] else "No MP"),
                f" · {r['mp_party']}" if r["mp_matched"] and pd.notna(r["mp_party"]) else "",
                class_="mb-1",
            ),
            *contact,
            ui.div(*chips, class_="mt-2"),
            ui.tags.div(
                f"Warnings in force: {int(r['severe_warnings'] or 0)} severe, "
                f"{int(r['warnings'] or 0)} warning, {int(r['alerts'] or 0)} alert.",
                class_="small mt-2",
            ),
        )

    # -------------------------------------------------------------------------
    # OUTPUT: the EA area list for the forecast day
    # Built as HTML rather than a Shiny DataGrid because the visual format
    # (coloured risk bar, two-line label) doesn't fit a tabular widget.
    # -------------------------------------------------------------------------
    @output
    @render.ui
    def ea_area_list():
        df = ea_areas()
        if df is None or df.empty:
            return ui.tags.p("No EA areas above threshold.", class_="text-muted small")

        rows = []
        for _, r in df.head(20).iterrows():
            share = "line" if pd.isna(r["intersection_pct"]) else f"{r['intersection_pct']*100:.0f}%"
            rows.append(
                ui.div(
                    ui.div(class_=f"risk-bar {r['risk_colour']}"),
                    ui.div(
                        ui.tags.strong(r["ea_area_name"]),
                        ui.tags.br(),
                        ui.tags.small(
                            f"{r['ea_area_type']} · {r['source']} · {share}",
                            class_="text-muted",
                        ),
                    ),
                    class_="area-row",
                )
            )

        if len(df) > 20:
            rows.append(
                ui.tags.small(
                    f"+ {len(df) - 20} more",
                    class_="text-muted d-block text-center mt-2",
                )
            )
        return ui.tags.div(*rows)

    # -------------------------------------------------------------------------
    # OUTPUT: the Leaflet map
    # Layers, bottom to top: constituency choropleth (worst forecast risk),
    # FGS polygons, warning and alert areas in force.
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

        if input.layer_constituencies():
            gdf = constituency_shapes()
            if gdf is not None and not gdf.empty:
                def const_style(p):
                    rank = _rank(p.get("fgs_worst_rank"))
                    warned = bool(p.get("has_active_warning"))
                    return {
                        "color":       "#212529" if warned else RANK_HEX[rank],
                        "fillColor":   RANK_HEX[rank],
                        "weight":      2.5 if warned else 1,
                        "fillOpacity": 0.35 if rank else 0.05,
                    }
                m.add(_styled_layer(gdf, const_style,
                                    {"weight": 3, "fillOpacity": 0.6}, "Constituencies"))

        gdf = polygons()
        if input.layer_polygons() and gdf is not None and not gdf.empty:
            def poly_style(p):
                colour = RISK_COLOUR_HEX.get(p.get("risk_colour"), "#555")
                return {"color": colour, "fillColor": colour, "weight": 1.5, "fillOpacity": 0.45}
            m.add(_styled_layer(gdf, poly_style,
                                {"weight": 3, "fillOpacity": 0.65}, "FGS polygons"))

        if input.layer_ea_areas():
            try:
                wgdf = warning_shapes()
            except Exception:
                wgdf = None
            if wgdf is not None and not wgdf.empty:
                def warn_style(p):
                    colour = SEVERITY_HEX.get(_rank(p.get("severity_level")), "#555")
                    return {"color": colour, "fillColor": colour, "weight": 1.5, "fillOpacity": 0.5}
                m.add(_styled_layer(wgdf, warn_style,
                                    {"weight": 3, "fillOpacity": 0.7}, "Warnings and alerts in force"))

        return m

    # -------------------------------------------------------------------------
    # EFFECT: move the map to the selected constituency
    # Moves the existing widget. Only constituencies drawn on the map (those
    # with a forecast risk or a warning) can be centred; others are ignored.
    # -------------------------------------------------------------------------
    @reactive.effect
    def _zoom_to_selection():
        r = selected()
        gdf = constituency_shapes()
        if r is None or gdf is None or gdf.empty:
            return
        hit = gdf[gdf["constituency_id"] == r["constituency_id"]]
        if hit.empty:
            return
        minx, miny, maxx, maxy = hit.total_bounds
        with reactive.isolate():
            try:
                live_map.widget.fit_bounds([[miny, minx], [maxy, maxx]])
            except Exception:
                pass  # a missing widget must never break the session
