# =============================================================================
# app.py
# Entry point for the FGS Flood Guidance Shiny dashboard.
#
# Responsibility: assemble the UI and server callbacks into a Shiny App.
# All UI lives in ui.py, all reactive logic in server.py, all data access in
# data.py. This file does no work itself.
#
# Run locally:  shiny run --reload app.py
# Deploy:       rsconnect deploy shiny . (or via Posit Connect Publisher)
# =============================================================================

from shiny import App
from server import server
from ui import app_ui

app = App(app_ui, server)
