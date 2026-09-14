"""Repo paths. `RAIL_DATASET_DIR` (env) points the solvers at another leg folder — the
scoring release at 16h00 — without touching the scorer, which always reads practice."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASETS = ROOT / "datasets"
PRACTICE = DATASETS / "practice"
LEGS = Path(os.environ["RAIL_DATASET_DIR"]).resolve() if os.environ.get("RAIL_DATASET_DIR") else PRACTICE
REF = DATASETS / "reference_data"
OSM_NETWORK = REF / "osm" / "belgium_rail_network.geojson"
OSM_STATIONS = REF / "osm" / "belgium_rail_stations.geojson"
GTFS_DB = REF / "gtfs_schedule" / "nmbs_schedule.sqlite"
CELLS_CSV = REF / "celltower" / "flanders_cells.csv"
SCORER = ROOT / "scorer"
WORK = ROOT / "work"
CACHE = WORK / "cache"

TZ = "Europe/Brussels"


def leg_dir(leg_id: str) -> Path:
    return LEGS / leg_id


def legs() -> list[str]:
    """Every leg folder (with a sensors.db) in the active dataset dir."""
    return sorted(p.name for p in LEGS.iterdir() if (p / "sensors.db").exists())


practice_legs = legs  # historical name used by the dev scripts
