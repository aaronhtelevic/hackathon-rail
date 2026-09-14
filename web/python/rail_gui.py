"""Write algorithm progress into the run directory the GUI reads.

Drop-in for any of the lanes::

    from web.python.rail_gui import RunWriter

    run = RunWriter(algorithm="motion-lane", lane="motion")
    leg = run.leg("ic830_00")
    leg.event("O1", "orientation recovered", pct=0.2)
    leg.write_json("shape.json", shape)
    leg.status("done", stage="M3", pct=1.0)
    run.finish()

Everything is best-effort and non-fatal: the GUI must never be able to break
a scoring run. All writes are atomic (tmp file + os.replace) except
``events.ndjson``, which is append-only so the server can tail it by offset.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

__all__ = ["RunWriter", "LegWriter", "runs_dir"]

_REPO_ROOT = Path(__file__).resolve().parents[2]


def runs_dir() -> Path:
    """Root of the run directory. Override with ``RAIL_RUNS_DIR``."""
    return Path(os.environ.get("RAIL_RUNS_DIR", _REPO_ROOT / "work" / "runs"))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class LegWriter:
    """Per-leg directory: status, contracts, events, outputs."""

    def __init__(self, run: "RunWriter", leg_id: str) -> None:
        self.run = run
        self.leg_id = leg_id
        self.dir = run.dir / leg_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._events = self.dir / "events.ndjson"
        self.status("running", stage="start", pct=0.0)

    # -- status -----------------------------------------------------------
    def status(self, state: str, *, stage: str | None = None,
               pct: float | None = None, message: str | None = None,
               **extra: Any) -> None:
        """state: 'queued' | 'running' | 'done' | 'error'."""
        payload = {
            "leg_id": self.leg_id,
            "state": state,
            "lane": self.run.lane,
            "stage": stage,
            "pct": pct,
            "message": message,
            "updated_at": _now_ms(),
            **extra,
        }
        self.write_json("status.json", payload)

    # -- events -----------------------------------------------------------
    def event(self, stage: str, msg: str, *, level: str = "info",
              pct: float | None = None, **extra: Any) -> None:
        """Append one line to events.ndjson; also bumps status.json."""
        record = {"at": _now_ms(), "stage": stage, "msg": msg, "level": level}
        if pct is not None:
            record["pct"] = pct
        record.update(extra)
        try:
            with self._events.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
                fh.flush()
        except OSError:
            return
        self.status("running", stage=stage, pct=pct, message=msg)

    # -- artifacts --------------------------------------------------------
    def write_json(self, name: str, obj: Any) -> None:
        try:
            _atomic_write(self.dir / name, json.dumps(obj, default=str, indent=2))
        except OSError:
            pass

    def write_text(self, name: str, text: str) -> None:
        try:
            _atomic_write(self.dir / name, text)
        except OSError:
            pass

    def shape(self, obj: Any) -> None:
        """The motion lane's half of the join contract."""
        self.write_json("shape.json", obj)

    def anchors(self, obj: Any) -> None:
        """The absolute lane's half of the join contract."""
        self.write_json("anchors.json", obj)

    def hydrated(self, polyline: list, **extra: Any) -> None:
        """Hydration output: [{'lat':..,'lon':..,'t':..,'distance_m':..}, ...]."""
        self.write_json("hydrated.json", {"leg_id": self.leg_id,
                                          "polyline": polyline, **extra})

    def score(self, obj: Any) -> None:
        """Whatever ``scorer.score_leg()`` returned for this leg."""
        self.write_json("score.json", obj)

    def done(self, message: str | None = None) -> None:
        self.status("done", stage="done", pct=1.0, message=message)

    def error(self, message: str) -> None:
        self.event("error", message, level="error")
        self.status("error", stage="error", message=message)

    def __enter__(self) -> "LegWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.error(f"{exc_type.__name__}: {exc}")
        elif (self.dir / "status.json").exists():
            self.done()
        return False


class RunWriter:
    """One directory per algorithm invocation."""

    def __init__(self, algorithm: str, *, lane: str = "joint",
                 run_id: str | None = None, track: str | None = None,
                 notes: str | None = None, root: Path | None = None) -> None:
        self.algorithm = algorithm
        self.lane = lane
        self.run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{algorithm}"
        self.dir = (root or runs_dir()) / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._legs: dict[str, LegWriter] = {}
        self.meta = {
            "run_id": self.run_id,
            "algorithm": algorithm,
            "lane": lane,
            "track": track,
            "notes": notes,
            "state": "running",
            "started_at": _now_ms(),
            "finished_at": None,
            "git_sha": os.environ.get("GIT_SHA"),
        }
        self._flush()

    def _flush(self) -> None:
        try:
            _atomic_write(self.dir / "run.json",
                          json.dumps(self.meta, default=str, indent=2))
        except OSError:
            pass

    def leg(self, leg_id: str) -> LegWriter:
        if leg_id not in self._legs:
            self._legs[leg_id] = LegWriter(self, leg_id)
        return self._legs[leg_id]

    def finish(self, state: str = "done", **extra: Any) -> None:
        self.meta.update(state=state, finished_at=_now_ms(), **extra)
        self._flush()

    def __enter__(self) -> "RunWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.finish("error" if exc else "done")
        return False
