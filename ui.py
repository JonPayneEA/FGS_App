# =============================================================================
# ui.py
# Declarative UI for the FGS Flood Guidance dashboard.
#
# Responsibility: define the layout, controls, and output slots.
# All dynamic content gets rendered by callbacks in server.py.
#
# Layout: the Today tab is table-first. A status strip and headline cards sit
# above a ranked constituency table (left of centre) with a linked map
# (right of centre); filters are in the left sidebar. Later phases add the
# What changed, Briefings, Exposure history and Pipeline health tabs.
# =============================================================================

from shiny import ui
from shinywidgets import output_widget

# Cell metadata for the 4x4 FGS risk matrix.
# Order matters: rows top-to-bottom are likelihood High to Very Low.
# Each cell is keyed by (impact_x, likelihood_y). Colour follows FGS Table 1.
RISK_MATRIX = [
    # (impact_x, likelihood_y, colour)
    (1, 4, "green"), (2, 4, "yellow"), (3, 4, "amber"),  (4, 4, "red"),
    (1, 3, "green"), (2, 3, "yellow"), (3, 3, "amber"),  (4, 3, "amber"),
    (1, 2, "green"), (2, 2, "green"),  (3, 2, "yellow"), (4, 2, "amber"),
    (1, 1, "green"), (2, 1, "green"),  (3, 1, "yellow"), (4, 1, "yellow"),
]


def matrix_filter() -> ui.Tag:
    """Build the 4x4 risk matrix as a grid of checkboxes styled as cells.

    Each cell is an input_checkbox with id 'mtx_{x}_{y}'. The server reads
    these to assemble a list of (x, y) pairs included in the filter.
    Default selection: any cell that resolves to Amber or Red.
    """
    cells = []
    for x, y, colour in RISK_MATRIX:
        # Amber/Red default on; Green/Yellow default off.
        # Forecasters care about elevated risk first, then drill down.
        default = colour in ("amber", "red")
        cells.append(
            ui.div(
                ui.input_checkbox(f"mtx_{x}_{y}", "", value=default),
                class_=f"matrix-cell {colour}",
                **{"data-x": str(x), "data-y": str(y)},
            )
        )

    return ui.div(
        ui.h6("Risk matrix position"),
        ui.tags.small("Tick cells to include · counts are live",
                      class_="text-muted d-block mb-2"),
        ui.div(*cells, class_="risk-matrix-grid"),
        ui.div("↑ Likelihood   ·   Impact →", class_="matrix-axis-label"),
    )


# -----------------------------------------------------------------------------
# SIDEBAR (left column)
# -----------------------------------------------------------------------------
sidebar = ui.div(
    ui.h6("Constituency table"),
    ui.input_select(
        "min_rank",
        "Show forecast risk of at least",
        choices={"1": "Very Low", "2": "Low", "3": "Medium", "4": "High"},
        selected="3",
    ),
    ui.input_checkbox("only_escalated", "Only escalated since last statement", value=False),
    ui.tags.small("Constituencies with a warning or alert in force always show.",
                  class_="text-muted d-block mb-2"),

    ui.h6("Map layers", class_="mt-3"),
    ui.input_checkbox("layer_constituencies", "Constituencies (worst risk)", value=True),
    ui.input_checkbox("layer_polygons", "FGS risk polygons", value=False),
    ui.input_checkbox("layer_ea_areas", "Warnings and alerts in force", value=True),

    ui.h6("Polygon source", class_="mt-3"),
    ui.input_checkbox("src_river",   "River", value=True),
    ui.input_checkbox("src_surface", "Surface", value=True),
    ui.input_checkbox("src_coastal", "Coastal", value=True),
    ui.input_checkbox("src_ground",  "Groundwater", value=False),

    matrix_filter(),

    ui.h6("EA area intersection threshold", class_="mt-3"),
    ui.input_slider("intersect_threshold", "", min=0, max=100, value=25, post="%"),

    ui.h6("Forecast day", class_="mt-3"),
    ui.input_select(
        "day_index",
        "",
        choices={"1": "Day 1", "2": "Day 2", "3": "Day 3", "4": "Day 4", "5": "Day 5"},
        selected="1",
    ),

    class_="shiny-sidebar",
)


# -----------------------------------------------------------------------------
# STATUS STRIP (pipeline health, statement banner, headline cards)
# -----------------------------------------------------------------------------
def _card(output_id: str, label: str, accent: bool = False) -> ui.Tag:
    return ui.div(
        ui.div(ui.output_text(output_id), class_="stat-num accent" if accent else "stat-num"),
        ui.div(label, class_="stat-label"),
        class_="stat-card",
    )


status_strip = ui.div(
    ui.output_ui("health_strip"),
    ui.output_ui("statement_banner"),
    ui.div(
        _card("card_medium_plus", "Constituencies at Medium or above", accent=True),
        _card("card_escalated", "Escalated since last statement"),
        _card("card_warnings", "With a warning in force"),
        _card("card_alerts", "With a flood alert in force"),
        class_="stat-row",
    ),
    class_="mb-3",
)


# -----------------------------------------------------------------------------
# CENTRE (constituency table and detail)
# -----------------------------------------------------------------------------
table_panel = ui.div(
    ui.div(
        ui.h5(ui.output_text("map_title", inline=True)),
        ui.tags.small(ui.output_text("map_subtitle", inline=True), class_="text-muted"),
        class_="mb-2",
    ),
    ui.output_data_frame("situation_table"),
    ui.div(ui.output_ui("constituency_detail"), class_="panel-section mt-3"),
    class_="p-3",
)


# -----------------------------------------------------------------------------
# RIGHT (map and the EA areas for the forecast day)
# -----------------------------------------------------------------------------
map_panel = ui.div(
    output_widget("live_map", height="520px"),
    ui.div(
        ui.h6("EA areas affected by the forecast", class_="panel-h"),
        ui.output_ui("ea_area_list"),
        class_="panel-section mt-3",
    ),
    class_="p-3",
)


# -----------------------------------------------------------------------------
# CUSTOM CSS
# Kept inline rather than in /static to keep the deploy self-contained.
# -----------------------------------------------------------------------------
custom_css = """
body { background: #f8f9fa; font-size: 14px; }
.shiny-sidebar { background: #e9ecef; padding: 20px; height: 100%; }
.shiny-sidebar h6 {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
  color: #495057; margin-bottom: 8px;
}
.stat-card {
  background: #fff; border: 1px solid #dee2e6; border-radius: 4px;
  padding: 12px; text-align: center;
}
.stat-num { font-size: 24px; font-weight: 700; line-height: 1; color: #212529; }
.stat-num.accent { color: #c8581f; }
.stat-label {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em;
  color: #6c757d; margin-top: 4px;
}
.stat-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.health-strip {
  display: flex; gap: 12px; align-items: center; padding: 6px 12px;
  border-radius: 4px; font-size: 12px; margin-bottom: 8px;
}
.health-strip.ok   { background: #e6f4ea; color: #1e5a2e; }
.health-strip.info { background: #e7f1fa; color: #1c4a73; }
.health-strip.warn { background: #fff4d6; color: #7a5a00; }
.health-strip.fail { background: #fbe4e4; color: #8a1c1c; }
.banner {
  padding: 8px 12px; border-radius: 4px; font-size: 13px; margin-bottom: 8px;
  background: #fff4d6; color: #7a5a00; border: 1px solid #f0d890;
}
.rank-chip {
  display: inline-block; min-width: 22px; text-align: center; border-radius: 3px;
  padding: 0 4px; font-size: 11px; font-weight: 600; color: #212529;
}
.rank-0 { background: #e9ecef; color: #6c757d; }
.rank-1 { background: #4f9d4f; color: #fff; }
.rank-2 { background: #f0d040; }
.rank-3 { background: #e08020; color: #fff; }
.rank-4 { background: #b02020; color: #fff; }
.panel-section {
  background: #fff; border: 1px solid #dee2e6; border-radius: 4px;
  padding: 14px; margin-bottom: 12px;
}
.panel-h {
  font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
  color: #495057; padding-bottom: 6px; border-bottom: 1px solid #e9ecef;
}
.area-row {
  display: flex; align-items: center; padding: 6px 0;
  border-bottom: 1px solid #f1f3f5; font-size: 13px;
}
.area-row:last-child { border-bottom: none; }
.risk-bar {
  width: 4px; height: 22px; margin-right: 10px; border-radius: 1px;
}
.risk-bar.Red    { background: #b02020; }
.risk-bar.Amber  { background: #e08020; }
.risk-bar.Yellow { background: #f0d040; }
.risk-bar.Green  { background: #4f9d4f; }

/* Risk matrix grid */
.risk-matrix-grid {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 2px; margin-bottom: 4px; max-width: 160px;
}
.matrix-cell {
  aspect-ratio: 1; position: relative; min-width: 0; overflow: hidden;
  display: flex; align-items: center; justify-content: center;
}
.matrix-cell .form-check {
  margin: 0; padding: 0;
}
.matrix-cell .form-check-input {
  margin: 0; cursor: pointer;
}
.matrix-cell.green  { background: #4f9d4f; }
.matrix-cell.yellow { background: #f0d040; }
.matrix-cell.amber  { background: #e08020; }
.matrix-cell.red    { background: #b02020; }
.matrix-axis-label {
  font-size: 10px; color: #495057; text-align: center; margin-top: 4px;
}
"""


# -----------------------------------------------------------------------------
# APP UI (top level)
# -----------------------------------------------------------------------------
app_ui = ui.page_fluid(
    ui.tags.style(custom_css),
    ui.navset_tab(
        ui.nav_panel(
            "Today",
            status_strip,
            ui.row(
                ui.column(2, sidebar),
                ui.column(6, table_panel),
                ui.column(4, map_panel),
            ),
        ),
        # Phase 2: What changed, Briefings. Phase 3: Exposure history,
        # Pipeline health.
        id="main_tabs",
    ),
    title="FGS Flood Guidance",
)
