"""Solve every practice leg on the warm track with the absolute-only solver and score it.

    .venv/bin/python scripts/run_warm.py [--team NAME] [--legs ic830_00_kortrijk_ingelmunster ...]
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from absolute import harness, paths, solve  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="televic")
    ap.add_argument("--legs", nargs="*")
    a = ap.parse_args()
    legs = a.legs or paths.practice_legs()
    for leg in legs:
        info = solve.solve_warm(leg, a.team)
        print(f"{leg[:44]:44s} cand={info['n_candidates']:2d} len={info['path_len_m']}  {info['hop']}")
    df = harness.table(a.team, "warm", legs)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    print(df.drop(columns=[c for c in ("ttffS", "ffErrM") if c in df]).to_string(index=False))
    print(harness.summary(df))
    (paths.WORK / "warm_scores.csv").write_text(df.to_csv(index=False))


if __name__ == "__main__":
    main()
