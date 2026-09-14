from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASETS = ROOT / "datasets"
PRACTICE = DATASETS / "practice"
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
    return PRACTICE / leg_id


def practice_legs() -> list[str]:
    return sorted(p.name for p in PRACTICE.iterdir() if (p / "sensors.db").exists())
