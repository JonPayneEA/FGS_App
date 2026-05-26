# =============================================================================
# data.py
# All data access for the dashboard.
#
# Responsibility:
#   - Hold the Databricks SQL connection
#   - Provide one function per query used by the dashboard
#   - Return pandas / geopandas frames ready for the UI to consume
#
# The SQL itself is kept verbose so it can be lifted into Databricks SQL and
# run directly for debugging. No ORM, no query builder. If you change a column
# name in the Delta tables, this is the only file you change.
#
# Environment variables required (set in .env locally, in Posit Connect's
# environment variable panel for deployment):
#   DATABRICKS_SERVER_HOSTNAME    e.g. adb-1234567890.0.azuredatabricks.net
#   DATABRICKS_HTTP_PATH          e.g. /sql/1.0/warehouses/abc123def456
#   DATABRICKS_TOKEN              personal access token or service principal
#   FGS_CATALOG                   Unity Catalog name, e.g. lab
#   FGS_SCHEMA                    Schema name, e.g. fgs_dev
# =============================================================================

import os
from datetime import date
from typing import Optional

import geopandas as gpd
import pandas as pd
from databricks import sql as dbsql
from dotenv import load_dotenv
from shapely import wkt

load_dotenv()


# -----------------------------------------------------------------------------
# CONNECTION
# A new connection is opened per query rather than held open. Posit Connect
# can suspend idle apps, which breaks long-held connections silently. Opening
# fresh on each call costs a few hundred milliseconds and avoids that class
# of failure entirely.
# -----------------------------------------------------------------------------

def _connection():
    return dbsql.connect(
        server_hostname=os.environ["DATABRICKS_SERVER_HOSTNAME"],
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DATABRICKS_TOKEN"],
    )


def _q(query: str, params: Optional[dict] = None) -> pd.DataFrame:
    """Run a SQL query and return a pandas DataFrame. Parameters bind safely."""
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or {})
            return cur.fetchall_arrow().to_pandas()


# Fully qualified table names assembled from env vars so dev/prod swap cleanly.
_CAT = os.environ.get("FGS_CATALOG", "lab")
_SCH = os.environ.get("FGS_SCHEMA", "fgs_dev")

TBL_STATEMENTS       = f"{_CAT}.{_SCH}.fgs_statements"
TBL_RISK_POLYGONS    = f"{_CAT}.{_SCH}.fgs_risk_polygons"
TBL_EA_INTERSECT     = f"{_CAT}.{_SCH}.fgs_ea_area_intersections"
TBL_CONST_INTERSECT  = f"{_CAT}.{_SCH}.fgs_constituency_intersections"
TBL_EA_FWA           = f"{_CAT}.{_SCH}.ea_flood_warning_areas"
TBL_EA_FAA           = f"{_CAT}.{_SCH}.ea_flood_alert_areas"


# =============================================================================
# QUERIES
# =============================================================================

def fetch_latest_statement() -> dict:
    """Return the metadata row for the most recently issued FGS statement.

    Used to populate the title, date, and 'last refresh' banner."""
    df = _q(f"""
        SELECT
          statement_id,
          issued_at,
          headline,
          flood_risk_trend_day1,
          flood_risk_trend_day2,
          flood_risk_trend_day3,
          flood_risk_trend_day4,
          flood_risk_trend_day5
        FROM {TBL_STATEMENTS}
        ORDER BY issued_at DESC
        LIMIT 1
    """)
    if df.empty:
        return {}
    return df.iloc[0].to_dict()


def fetch_risk_polygons(statement_id: int, day_index: int) -> gpd.GeoDataFrame:
    """Return all risk polygons for a given statement and forecast day.

    The geometry column comes back as WKT from Mosaic's st_astext, which
    geopandas parses into shapely geometries. CRS is WGS84 (EPSG:4326).
    """
    df = _q(f"""
        SELECT
          poly_id,
          source,
          day_index,
          forecast_date,
          risk_x,
          risk_y,
          impact_label,
          likelihood_label,
          risk_level,
          risk_colour,
          ST_ASTEXT(geometry) AS geometry_wkt
        FROM {TBL_RISK_POLYGONS}
        WHERE statement_id = :sid
          AND day_index = :day
    """, {"sid": statement_id, "day": day_index})

    if df.empty:
        return gpd.GeoDataFrame(columns=df.columns, geometry=[], crs="EPSG:4326")

    df["geometry"] = df["geometry_wkt"].apply(wkt.loads)
    df = df.drop(columns=["geometry_wkt"])
    return gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")


def fetch_ea_areas_for_statement(statement_id: int, day_index: int) -> pd.DataFrame:
    """Return EA flood areas affected by a given statement on a given day.

    Ordered by risk level (highest first) so the right-hand panel reads
    top-down by severity. Intersection percentage carried through for display.
    """
    return _q(f"""
        SELECT
          i.ea_area_code,
          i.ea_area_name,
          i.ea_area_type,
          i.source,
          i.risk_level,
          i.risk_colour,
          i.risk_x,
          i.risk_y,
          i.intersection_pct
        FROM {TBL_EA_INTERSECT} i
        WHERE i.statement_id = :sid
          AND i.day_index = :day
        ORDER BY
          CASE i.risk_colour
            WHEN 'Red'    THEN 1
            WHEN 'Amber'  THEN 2
            WHEN 'Yellow' THEN 3
            WHEN 'Green'  THEN 4
            ELSE 5
          END,
          i.intersection_pct DESC
    """, {"sid": statement_id, "day": day_index})


def fetch_summary_counts(statement_id: int, day_index: int) -> dict:
    """Return headline counts for the top of the right-hand panel.

    One query, four counts via conditional aggregation. Cheaper than four
    separate round trips.
    """
    df = _q(f"""
        SELECT
          (SELECT COUNT(*) FROM {TBL_RISK_POLYGONS}
            WHERE statement_id = :sid AND day_index = :day) AS polygon_count,
          (SELECT COUNT(DISTINCT ea_area_code) FROM {TBL_EA_INTERSECT}
            WHERE statement_id = :sid AND day_index = :day) AS ea_area_count,
          (SELECT COUNT(DISTINCT constituency_id) FROM {TBL_CONST_INTERSECT}
            WHERE statement_id = :sid AND day_index = :day) AS constituency_count
    """, {"sid": statement_id, "day": day_index})

    if df.empty:
        return {"polygon_count": 0, "ea_area_count": 0, "constituency_count": 0}
    return df.iloc[0].to_dict()
