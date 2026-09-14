"""Joint run: motion shape + absolute anchors/position, one pass per leg.

Both lane outputs feed hydration/OSM-snap, so a leg is only useful solved both
ways at once -- there is no standalone "motion run" or "absolute run" anymore.

Two ways to run a leg, same submission either way:

  batch (default)  motion.shape_stream.run_leg() writes the whole shape.json,
                   then absolute.solve.solve_warm() builds every anchor and
                   hydrates once. Fastest; this is what the 50-leg scoring
                   sweep uses.
  --stream         joint.stream.solve_leg(): shape and anchors advance together
                   on one leg-time clock and the hydration DP is re-run on the
                   prefix as it goes, so hydrated points come out as a third
                   stream while the leg plays. This is what the viewer watches;
                   --demo-speed paces it.

Both write into the same RunWriter leg entry (lane="joint"), so the viewer
shows shape + anchors + hydrated + score together per leg.

    .venv/bin/python scripts/run_joint.py [--team NAME] [--legs ic830_00 ...]
    .venv/bin/python scripts/run_joint.py --stream --demo-speed 60 --legs ic830_00_...
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
from joint import stream  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="televic")
    ap.add_argument("--legs", nargs="*")
    ap.add_argument("--run-id", dest="run_id")
    ap.add_argument("--notes", default="joint run: motion shape + absolute anchors/position per leg")
    ap.add_argument("--stream", action="store_true",
                    help="run the synchronized shape+anchor stream (joint/stream.py) instead of "
                         "the batch lanes — emits hydrated points as the leg plays")
    ap.add_argument("--demo-speed", type=float, default=0.0, metavar="X",
                    help="pace the run to X-times realtime so the viewer draws the "
                         "shape/timeline/map live (0 = flat-out, the default)")
    ap.add_argument("--refit-every", type=float, default=stream.REFIT_EVERY_S, metavar="S",
                    help="--stream only: leg-seconds between prefix hydration refits")
    a = ap.parse_args()
    legs = a.legs or paths.practice_legs()
    cfg = stream.StreamConfig(refit_every_s=a.refit_every, demo_speed=a.demo_speed)
    with RunWriter("joint", lane="joint", track="warm", run_id=a.run_id, notes=a.notes) as run:
        for leg in legs:
            lw = run.leg(leg)
            if a.stream:
                info = stream.solve_leg(leg, a.team, gui=lw, cfg=cfg)
            else:
                motion_run_leg(str(paths.PRACTICE), leg, str(paths.WORK), run=run,
                               demo_speed=a.demo_speed)
                info = solve.solve_warm(leg, a.team, gui=lw)
            rep = harness.score(leg, "warm", paths.WORK / "submissions" / a.team / "warm" / leg)
            lw.score(rep)
            f = harness.flatten(rep)
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
