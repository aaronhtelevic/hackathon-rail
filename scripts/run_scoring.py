"""Final-round runner (X3–X5): solve every leg of a dataset on both tracks, validate the
CSVs, and lay them out as <out>/<team>/<cold|warm>/<leg_id>/{position,station_calls}.csv.

    .venv/bin/python scripts/run_scoring.py --dataset datasets/scoring_release --out work/final
    .venv/bin/python scripts/run_scoring.py --dataset datasets/practice --out work/final_practice --score

Warm needs the three warm-start fields in the leg's meta.json (stationFrom, coordFrom,
tFromEpochMillis) — nothing else is read from it. Cold infers them from cell towers
(absolute/cold.py); a leg with no usable towers gets **no cold folder** (a wild fix scores
worse than none). The motion lane's shape.json is produced here first (motion/shape_stream.py)
so hydration has it. `--score` runs the organizers' scorer afterwards (practice only).
"""
import argparse
import os
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ap = argparse.ArgumentParser()
ap.add_argument("--dataset", required=True, type=Path)
ap.add_argument("--out", required=True, type=Path)
ap.add_argument("--team", default="televic")
ap.add_argument("--legs", nargs="*")
ap.add_argument("--tracks", nargs="*", default=["warm", "cold"], choices=["warm", "cold"])
ap.add_argument("--score", action="store_true", help="score with scorer/ afterwards (practice legs only)")
ap.add_argument("--no-shape", action="store_true", help="skip motion/shape_stream.py (reuse work/<leg>/shape.json)")
args = ap.parse_args()

os.environ["RAIL_DATASET_DIR"] = str(args.dataset.resolve())  # must precede the absolute imports
from absolute import cold, paths, solve, track  # noqa: E402
from motion.shape_stream import run_leg as motion_run_leg  # noqa: E402

import pandas as pd  # noqa: E402


def validate(d: Path) -> list[str]:
    """X4: hard checks on one leg folder. Returns problems (empty = fine)."""
    problems = []
    pos = d / "position.csv"
    if not pos.exists():
        return ["position.csv missing"]
    p = pd.read_csv(pos)
    need = {"epochMillis", "latitude", "longitude"}
    if not need.issubset(p.columns):
        problems.append(f"position.csv columns {list(p.columns)}")
    else:
        if len(p) < 2:
            problems.append("position.csv has <2 rows")
        if p[["epochMillis", "latitude", "longitude"]].isna().any().any():
            problems.append("NaN in position.csv")
        if not p.epochMillis.is_monotonic_increasing:
            problems.append("epochMillis not monotonic")
        if not (p.latitude.between(49.4, 51.6).all() and p.longitude.between(2.4, 6.5).all()):
            problems.append("lat/lon outside Belgium")
        if "routeGuess" in p and p.routeGuess.dropna().empty:
            problems.append("routeGuess column present but empty")
    calls = d / "station_calls.csv"
    if calls.exists():
        c = pd.read_csv(calls)
        if not {"epochMillis", "stationNameGuess"}.issubset(c.columns):
            problems.append(f"station_calls.csv columns {list(c.columns)}")
        elif len(c) > 1:
            problems.append(f"{len(c)} station calls (only the first counts)")
    return problems


def main():
    out = args.out.resolve()
    legs = args.legs or paths.legs()
    print(f"dataset {paths.LEGS}  legs {len(legs)}  tracks {args.tracks}  out {out}")
    g = track.load()
    report = []
    for leg in legs:
        row = {"leg": leg}
        try:
            if not args.no_shape:
                motion_run_leg(str(paths.LEGS), leg, str(paths.WORK), want_svg=False)
        except Exception as e:  # hydration then falls back to the trapezoid
            row["shape"] = f"FAILED {type(e).__name__}: {e}"
            traceback.print_exc()
        for tr in args.tracks:
            try:
                if tr == "warm":
                    ws = solve.warm_from_meta(leg)
                    if ws is None:
                        row[tr] = "skipped: no warm fields"
                        continue
                else:
                    ws, _ = cold.cold_start(leg, g)
                    if ws is None:
                        row[tr] = "skipped: no cold fix"
                        continue
                info = solve.solve_warm(leg, args.team, ws=ws, out_root=out, sub_track=tr)
                d = out / args.team / tr / leg
                probs = validate(d)
                row[tr] = "ok " + (info.get("hop") or "no hop") if not probs else "INVALID " + "; ".join(probs)
                if probs:
                    shutil.rmtree(d, ignore_errors=True)  # never ship a malformed folder
            except Exception as e:
                row[tr] = f"FAILED {type(e).__name__}: {e}"
                traceback.print_exc()
        report.append(row)
        print(f"{leg[:44]:44s} warm: {str(row.get('warm'))[:60]:60s} cold: {str(row.get('cold'))[:50]}", flush=True)

    rep = pd.DataFrame(report)
    out.mkdir(parents=True, exist_ok=True)
    rep.to_csv(out / "run_report.csv", index=False)
    for tr in args.tracks:
        n = sum(str(r.get(tr, "")).startswith("ok") for r in report)
        print(f"{tr}: {n}/{len(legs)} legs written")
    print("layout:", out / args.team / "<track>" / "<leg_id>")

    if args.score:
        from absolute import harness
        for tr in args.tracks:
            df = harness.table(args.team, tr, legs, out)
            print(f"\n== {tr} ==")
            print(harness.summary(df))


if __name__ == "__main__":
    main()
