"""Joint run: motion shape + absolute anchors/position, one pass per leg.

Both lane outputs feed hydration/OSM-snap, so a leg is only useful solved
both ways at once -- there is no standalone "motion run" or "absolute run"
anymore. Per leg, in order:
  1. motion.shape_stream.run_leg()  -> work/<leg>/shape.json
  2. absolute.solve.solve_warm()    -> work/<leg>/anchors.json + submission
     (solve_warm hydrates shape.json written in step 1 onto the OSM path —
     absolute/hydrate.py — so the order is load-bearing, not incidental)
Both write into the same RunWriter leg entry (lane="joint" by default), so
the viewer shows shape + anchors + hydrated + score together per leg.

    .venv/bin/python scripts/run_joint.py [--team NAME] [--legs ic830_00 ...]
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web" / "python"))
from rail_gui import RunWriter  # noqa: E402
from motion.shape_stream import run_leg as motion_run_leg  # noqa: E402
from absolute import harness, paths, solve  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="televic")
    ap.add_argument("--legs", nargs="*")
    ap.add_argument("--run-id", dest="run_id")
    ap.add_argument("--notes", default="joint run: motion shape + absolute anchors/position per leg")
    ap.add_argument("--demo-speed", type=float, default=0.0, metavar="X",
                    help="pace both lanes' writes to X-times realtime so the viewer "
                         "draws the shape/timeline/map live (0 = flat-out, the default)")
    a = ap.parse_args()
    legs = a.legs or paths.practice_legs()
    with RunWriter("joint", lane="joint", track="warm", run_id=a.run_id, notes=a.notes) as run:
        for leg in legs:
            motion_run_leg(str(paths.PRACTICE), leg, str(paths.WORK), run=run, demo_speed=a.demo_speed)
            info = solve.solve_warm(leg, a.team, gui=run.leg(leg))
            rep = harness.score(leg, "warm", paths.WORK / "submissions" / a.team / "warm" / leg)
            run.leg(leg).score(rep)
            f = harness.flatten(rep)
            lw = run.leg(leg)
            lw.event("I3", f"median err {f['medErrM']} m, route {f['route']} "
                      f"{'OK' if f['routeOK'] else 'WRONG'}, "
                      f"station {'OK' if f['stationOK'] else 'miss'}", pct=1.0)
            lw.done(f"median err {f['medErrM']} m")
            print(f"{leg[:44]:44s} {info['hop']}  -> med {f['medErrM']} m")
    df = harness.table(a.team, "warm", legs)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    print(df.drop(columns=[c for c in ("ttffS", "ffErrM") if c in df]).to_string(index=False))
    print(harness.summary(df))
    (paths.WORK / "warm_scores.csv").write_text(df.to_csv(index=False))
    print("run dir:", run.dir)


if __name__ == "__main__":
    main()
