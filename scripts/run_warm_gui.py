"""Same as run_warm.py, but streams every leg into the web GUI (web/README.md).

    .venv/bin/python scripts/run_warm_gui.py [--team NAME] [--legs ...] [--notes TEXT]
Then `cd web && npm run dev` and open http://localhost:5173.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web" / "python"))
from rail_gui import RunWriter  # noqa: E402
from absolute import harness, paths, solve  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="televic")
    ap.add_argument("--legs", nargs="*")
    ap.add_argument("--notes", default="absolute-only warm solver: GTFS hop + OSM path + schedule timing")
    a = ap.parse_args()
    legs = a.legs or paths.practice_legs()
    with RunWriter("absolute-warm", lane="absolute", track="warm", notes=a.notes) as run:
        for leg in legs:
            with run.leg(leg) as lw:
                info = solve.solve_warm(leg, a.team, gui=lw)
                rep = harness.score(leg, "warm", paths.WORK / "submissions" / a.team / "warm" / leg)
                lw.score(rep)
                f = harness.flatten(rep)
                lw.event("I3", f"median err {f['medErrM']} m, route {f['route']} {'OK' if f['routeOK'] else 'WRONG'}, "
                               f"station {'OK' if f['stationOK'] else 'miss'}", pct=1.0)
                print(f"{leg[:44]:44s} {info['hop']}  -> med {f['medErrM']} m")
    print("run dir:", run.dir)


if __name__ == "__main__":
    main()
