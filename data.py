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
# The app follows the pipeline's own rules, so it never shows a different
# picture from the one the jobs publish:
#   - the current statement is the latest one whose intersections are complete
#     for the chosen geometry source (the rule in utils/helpers.py of the
#     pipeline, latest_complete_statements);
#   - every intersection query is filtered to that one geometry source, so
#     "risk_areas" and "aoc" rows are never counted together.
#
# Environment variables (set in .env locally, in Posit Connect's environment
# variable panel for deployment):
#   DATABRICKS_SERVER_HOSTNAME    e.g. adb-1234567890.0.azuredatabricks.net
#   DATABRICKS_HTTP_PATH          e.g. /sql/1.0/warehouses/abc123def456
#   DATABRICKS_TOKEN              personal access token or service principal
#   FGS_CATALOG                   default prd_dash_lab
#   FGS_SCHEMA                    default flood_forecasting_unrestricted
#   FGS_GEOMETRY_SOURCE           risk_areas (default) or aoc; match config.py
#                                 in the pipeline
#   FGS_CACHE_SECONDS             default 90; 0 turns the cache off
#
# The token needs SELECT on the unrestricted schema only. The app never reads
# the restricted schema (flood thresholds).
# =============================================================================

import functools
import json
import os
import time
from typing import Optional

import geopandas as gpd
import pandas as pd
from databricks import sql as dbsql
from dotenv import load_dotenv
from shapely.geometry import shape

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


# -----------------------------------------------------------------------------
# CACHE
# A short time-to-live cache shared by every session. The pipeline writes at
# most every few hours, so 90 seconds costs nothing in freshness and stops
# every filter change from reaching the warehouse.
# -----------------------------------------------------------------------------

_CACHE_SECONDS = float(os.environ.get("FGS_CACHE_SECONDS", "90"))
_cache: dict = {}


def _cached(fn):
    @functools.wraps(fn)
    def wrapper(*args):
        if _CACHE_SECONDS <= 0:
            return fn(*args)
        key = (fn.__name__, args)
        hit = _cache.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
        value = fn(*args)
        _cache[key] = (now, value)
        return value
    return wrapper


# -----------------------------------------------------------------------------
# CONFIGURATION
# Fully qualified table names assembled from env vars so dev/prod swap cleanly.
# Defaults match the pipeline's config.py.
# -----------------------------------------------------------------------------

_CAT = os.environ.get("FGS_CATALOG", "prd_dash_lab")
_SCH = os.environ.get("FGS_SCHEMA", "flood_forecasting_unrestricted")

# Validated, because it is placed in SQL text rather than bound as a parameter.
GEOMETRY_SOURCE = os.environ.get("FGS_GEOMETRY_SOURCE", "risk_areas")
if GEOMETRY_SOURCE not in ("risk_areas", "aoc"):
    raise ValueError(
        f"FGS_GEOMETRY_SOURCE must be 'risk_areas' or 'aoc', not {GEOMETRY_SOURCE!r}"
    )

TBL_STATEMENTS       = f"{_CAT}.{_SCH}.fgs_statements"
TBL_INTERSECT_LOG    = f"{_CAT}.{_SCH}.fgs_intersections_log"
TBL_RISK_POLYGONS    = f"{_CAT}.{_SCH}.fgs_risk_polygons"
TBL_EA_INTERSECT     = f"{_CAT}.{_SCH}.fgs_ea_area_intersections"
TBL_CONST_INTERSECT  = f"{_CAT}.{_SCH}.fgs_constituency_intersections"
TBL_EA_FWA           = f"{_CAT}.{_SCH}.ea_flood_warning_areas"
TBL_EA_FAA           = f"{_CAT}.{_SCH}.ea_flood_alert_areas"
TBL_CONSTITUENCIES   = f"{_CAT}.{_SCH}.parliamentary_constituencies"
TBL_SITUATION        = f"{_CAT}.{_SCH}.constituency_flood_situation"
TBL_WARN_CURRENT     = f"{_CAT}.{_SCH}.flood_warnings_current"
TBL_HEALTH           = f"{_CAT}.{_SCH}.pipeline_health"


def _in_source(alias: str) -> str:
    """SQL condition selecting rows of the configured geometry source.
    Rows written before the geometry_source column existed count as risk_areas,
    as in the pipeline's source_predicate."""
    return f"COALESCE({alias}.geometry_source, 'risk_areas') = '{GEOMETRY_SOURCE}'"


# Simplification tolerance for constituency boundaries, in degrees (about 50 m).
# The boundaries are already generalised (BGC); this trims the payload sent to
# the browser further.
_SIMPLIFY_DEG = 0.0005


def _geojson_to_gdf(df: pd.DataFrame, column: str = "geometry",
                    simplify: float = 0.0) -> gpd.GeoDataFrame:
    """Turn a frame holding a GeoJSON string column into a GeoDataFrame
    (EPSG:4326). Rows with missing or unreadable geometry are dropped."""
    def parse(value):
        if not isinstance(value, str):
            return None
        try:
            geom = shape(json.loads(value))
        except Exception:
            return None
        return geom.simplify(simplify, preserve_topology=True) if simplify else geom

    geoms = df[column].apply(parse)
    out = df.drop(columns=[column]).assign(geometry=geoms)
    out = out[out["geometry"].notna()]
    return gpd.GeoDataFrame(out, geometry="geometry", crs="EPSG:4326")


# =============================================================================
# QUERIES
# =============================================================================

@_cached
def fetch_current_statement() -> dict:
    """Return the statement the app should show, and whether a newer one waits.

    The shown statement is the latest whose intersections are complete and up
    to date for the configured geometry source: the same rule as pipeline jobs
    14 and 18. 'pending' is True when a newer statement exists but has not yet
    been intersected, so the app can say it is showing the previous one.
    """
    df = _q(f"""
        SELECT
          s.statement_id,
          s.issued_at,
          s.headline,
          s.flood_risk_trend_day1,
          s.flood_risk_trend_day2,
          s.flood_risk_trend_day3,
          s.flood_risk_trend_day4,
          s.flood_risk_trend_day5
        FROM {TBL_INTERSECT_LOG} l
        JOIN {TBL_STATEMENTS} s
          ON s.statement_id = l.statement_id
         AND COALESCE(s.last_modified_at, '') = COALESCE(l.last_modified_at, '')
        WHERE {_in_source('l')}
        ORDER BY s.issued_at DESC, l.statement_id DESC
        LIMIT 1
    """)
    if df.empty:
        return {}
    current = df.iloc[0].to_dict()

    newest = _q(f"SELECT MAX(issued_at) AS newest FROM {TBL_STATEMENTS}")
    newest_at = newest.iloc[0]["newest"] if not newest.empty else None
    current["pending"] = bool(
        newest_at is not None and pd.to_datetime(newest_at) > pd.to_datetime(current["issued_at"])
    )
    return current


@_cached
def fetch_risk_polygons(statement_id: int, day_index: int) -> gpd.GeoDataFrame:
    """Return all risk polygons for a given statement and forecast day.

    The geometry column comes back as WKT from Mosaic's st_astext, which
    geopandas parses into shapely geometries. CRS is WGS84 (EPSG:4326).
    """
    from shapely import wkt

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


@_cached
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
          AND {_in_source('i')}
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


@_cached
def fetch_summary_counts(statement_id: int, day_index: int) -> dict:
    """Return headline counts for the given statement and forecast day."""
    df = _q(f"""
        SELECT
          (SELECT COUNT(*) FROM {TBL_RISK_POLYGONS}
            WHERE statement_id = :sid AND day_index = :day) AS polygon_count,
          (SELECT COUNT(DISTINCT ea_area_code) FROM {TBL_EA_INTERSECT} i
            WHERE i.statement_id = :sid AND i.day_index = :day
              AND {_in_source('i')}) AS ea_area_count,
          (SELECT COUNT(DISTINCT constituency_id) FROM {TBL_CONST_INTERSECT} i
            WHERE i.statement_id = :sid AND i.day_index = :day
              AND {_in_source('i')}) AS constituency_count
    """, {"sid": statement_id, "day": day_index})

    if df.empty:
        return {"polygon_count": 0, "ea_area_count": 0, "constituency_count": 0}
    return df.iloc[0].to_dict()


@_cached
def fetch_constituency_situation() -> pd.DataFrame:
    """One row per constituency: forecast, warnings in force and the MP.

    Built by pipeline job 19 from the latest complete statement, so no
    ranking or joining happens here. Ranks: 1 Very Low, 2 Low, 3 Medium,
    4 High, 0 nothing flagged.
    """
    return _q(f"""
        SELECT
          constituency_id,
          constituency_name,
          mp_matched,
          mp_name,
          mp_party,
          mp_email,
          mp_phone,
          mp_website,
          fgs_statement_id,
          fgs_worst_rank,
          fgs_worst_level,
          fgs_worst_date,
          fgs_worst_sources,
          fgs_rank_day1,
          fgs_rank_day2,
          fgs_rank_day3,
          fgs_rank_day4,
          fgs_rank_day5,
          fgs_changed,
          fgs_escalated,
          severe_warnings,
          warnings,
          alerts,
          has_active_warning,
          computed_at
        FROM {TBL_SITUATION}
    """)


@_cached
def fetch_constituency_geometry(constituency_ids: tuple) -> gpd.GeoDataFrame:
    """Simplified boundaries for the given constituencies only.

    Only constituencies with a forecast risk or a warning are drawn, so the
    browser receives tens of shapes rather than all 650. Takes a tuple so the
    result can be cached on its arguments.
    """
    if not constituency_ids:
        return gpd.GeoDataFrame(columns=["constituency_id"], geometry=[], crs="EPSG:4326")

    # Ids are ONS codes read from our own table; bound by position to stay safe.
    marks = ", ".join(f":c{i}" for i in range(len(constituency_ids)))
    params = {f"c{i}": cid for i, cid in enumerate(constituency_ids)}
    df = _q(f"""
        SELECT constituency_id, geometry
        FROM {TBL_CONSTITUENCIES}
        WHERE constituency_id IN ({marks})
    """, params)
    return _geojson_to_gdf(df, simplify=_SIMPLIFY_DEG)


@_cached
def fetch_warning_areas_in_force() -> gpd.GeoDataFrame:
    """Flood warning and alert areas currently in force, with their shapes.

    Severity: 1 Severe Flood Warning, 2 Flood Warning, 3 Flood Alert.
    Severity 4 ("no longer in force") is excluded.
    """
    cols = """
          w.flood_area_id AS ea_area_code,
          a.ea_area_name,
          a.ea_area_type,
          w.severity_level,
          a.geometry
    """
    df = _q(f"""
        SELECT {cols}
        FROM {TBL_WARN_CURRENT} w
        JOIN {TBL_EA_FWA} a ON a.ea_area_code = w.flood_area_id
        WHERE w.severity_level IN (1, 2, 3)
        UNION ALL
        SELECT {cols}
        FROM {TBL_WARN_CURRENT} w
        JOIN {TBL_EA_FAA} a ON a.ea_area_code = w.flood_area_id
        WHERE w.severity_level IN (1, 2, 3)
    """)
    return _geojson_to_gdf(df, simplify=_SIMPLIFY_DEG)


@_cached
def fetch_pipeline_health() -> pd.DataFrame:
    """One row per pipeline health check (status ok, info, warn, fail)."""
    return _q(f"""
        SELECT check_name, status, value, detail, checked_at
        FROM {TBL_HEALTH}
    """)
