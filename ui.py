# =============================================================================
# ui.py
# Declarative UI for the FGS Flood Guidance dashboard.
#
# Responsibility: define the layout, controls, and output slots.
# All dynamic content gets rendered by callbacks in server.py.
#
# Layout: three columns (sidebar | map | right panel) inside a navset_tab
# that will eventually hold the analytics tabs as well. For now only the
# Live View tab exists.
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
    ui.h6("Map layers"),
    ui.input_checkbox("layer_polygons", "FGS risk polygons", value=True),
    ui.input_checkbox("layer_ea_areas", "EA flood areas", value=True),
    ui.input_checkbox("layer_constituencies", "Constituencies", value=False),

    ui.h6("Risk source", class_="mt-3"),
    ui.input_checkbox("src_river",   "River", value=True),
    ui.input_checkbox("src_surface", "Surface", value=True),
    ui.input_checkbox("src_coastal", "Coastal", value=True),
    ui.input_checkbox("src_ground",  "Groundwater", value=False),

    matrix_filter(),

    ui.h6("Intersection threshold", class_="mt-3"),
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
# CENTRE PANEL (map and statement title)
# -----------------------------------------------------------------------------
centre = ui.div(
    ui.div(
        ui.h5(ui.output_text("map_title", inline=True)),
        ui.tags.small(ui.output_text("map_subtitle", inline=True),
                      class_="text-muted"),
        class_="mb-3",
    ),
    output_widget("live_map", height="500px"),
    class_="p-3",
)


# -----------------------------------------------------------------------------
# RIGHT PANEL (EA area list + selected polygon detail)
# -----------------------------------------------------------------------------
right_panel = ui.div(
    ui.div(
        ui.div(
            ui.div(ui.output_text("count_polygons"), class_="stat-num accent"),
            ui.div("FGS polygons", class_="stat-label"),
            class_="stat-card",
        ),
        ui.div(
            ui.div(ui.output_text("count_ea_areas"), class_="stat-num"),
            ui.div("EA areas affected", class_="stat-label"),
            class_="stat-card mt-2",
        ),
        ui.div(
            ui.div(ui.output_text("count_constituencies"), class_="stat-num"),
            ui.div("Constituencies", class_="stat-label"),
            class_="stat-card mt-2",
        ),
        class_="mb-3",
    ),
    ui.div(
        ui.h6("EA areas affected", class_="panel-h"),
        ui.output_ui("ea_area_list"),
        class_="panel-section",
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
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 2px; margin-bottom: 4px;
}
.matrix-cell {
  aspect-ratio: 1; position: relative;
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
            "Live view",
            ui.row(
                ui.column(2, sidebar),
                ui.column(7, centre),
                ui.column(3, right_panel),
            ),
        ),
        # Analytics tabs added later. Placeholder kept so the navset renders.
        ui.nav_panel("Warning issue times", ui.p("Coming soon.")),
        ui.nav_panel("Forecast verification", ui.p("Coming soon.")),
        ui.nav_panel("Event playback",       ui.p("Coming soon.")),
        ui.nav_panel("Coverage gaps",        ui.p("Coming soon.")),
        ui.nav_panel("MP audit log",         ui.p("Coming soon.")),
        id="main_tabs",
    ),
    title="FGS Flood Guidance",
)
