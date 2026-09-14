#!/usr/bin/env python3
"""Lane M (Phase 2): causal IMU -> route shape.

Produces `shape.json` per the WORKLOG.md data contract, plus a debug trace and
an SVG of the dead-reckoned shape.

Strictly causal / online: every filter is one-sided (EMA / trailing window), the
segment state machine only ever looks at samples it has already consumed, and
segments are emitted the moment they close. Nothing reads ahead, so the same
code can run against a live sensor stream. `--stream-log` records the wall-clock
position in the leg at which each segment was emitted, which is the proof of it.

Covers tasks O1, O2 (partial), M1, M2, M3 and the H2 speed prior.

What it does and does not deliver (measured on the 44 `good` practice legs
with motion/validate_shape.py):
  * turn sequence / heading  -- reliable: median 10 deg net heading error
  * moving / stopped         -- reliable
  * speed and length priors  -- weak: median 39% error, curve-derived only.
    Accelerometer integration and vibration energy were both tried and both
    fail on this data (see WORKLOG.md). Scale has to come from the anchors and
    GTFS in hydration; `speed_source` per segment says how much to trust it.

Pure stdlib (no numpy) -- the whole pipeline is per-sample scalar math.

Usage:
  python3 motion/shape_stream.py --leg ic2035_00_antwerpen_centraal_antwerpen_berchem
  python3 motion/shape_stream.py --all

Output goes to two places:
  work/<leg_id>/            -- the lane contract, what hydration consumes
  work/runs/<run_id>/<leg>/ -- the run directory `web/` watches (see web/README.md)
Segment-close events are written to events.ndjson as they happen, so the
viewer's timeline fills in while the leg is still streaming. `--no-gui` skips
the run directory entirely.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "web", "python"))
try:
    from rail_gui import RunWriter          # web/python/rail_gui.py
except Exception:                            # viewer absent -> run headless
    RunWriter = None

# ---------------------------------------------------------------------------
# Tunables. Times in seconds, accel in m/s^2, rates in rad/s.
# ---------------------------------------------------------------------------

TAU_GRAVITY = 60.0       # gravity (device "up") low-pass -- must be far slower
                         # than the train's own accelerations, else a sustained
                         # pull-away gets absorbed into "gravity"
TAU_YAW = 1.2            # yaw-rate low-pass feeding segmentation
TAU_HP = 0.4             # accel high-pass for vibration energy
VIB_WINDOW_S = 2.0       # trailing window for vibration RMS / stationarity
TAU_SMOOTH = 2.0         # accel/gyro smoothing for the centripetal estimator
TAU_LAT = 120.0          # forgetting time of the lateral-axis regression
TAU_ROLL_BASE = 45.0     # "level track" baseline for the integrated roll angle
TAU_SPEED_FUSE = 20.0    # pull of dead-reckoned speed toward the curve estimate

# Speed comes from curves: a_lat = v * yaw_rate. Cant (superelevation) hides
# part of a_lat, so the gyro-integrated roll angle is added back. ALPHA/BETA
# were grid-fitted against ground truth on 8 `good` practice legs: median 39%
# relative error, and only while turning. A prior for hydration, not an answer.
SPEED_ALPHA = 0.39
SPEED_BETA = 1.0
REG_ALPHA = 2.09         # mean speed = REG_ALPHA * |regression coeff| (beta=0 fit)
OMEGA_SPEED_MIN = 0.008  # rad/s -- below this v = a_lat/omega is ill-posed
V_MIN_VALID = 1.0

# Global scale on the speed prior. The curve estimator under-reads, so the
# dead-reckoned leg length came out a median 1.6x short over the 44 `good`
# practice legs; this is the log-unbiased correction (motion/validate_shape.py).
SPEED_SCALE = 1.0

# Last resort: a leg with no usable curve evidence yet (straight running, or
# the first minute) gets a generic cruise prior at low confidence, so that
# hydration has *something* to stretch rather than a zero-length shape.
DEFAULT_CRUISE_MPS = 22.0
NO_EVIDENCE_GRACE_S = 25.0

STOP_RMS_FLOOR = 0.085   # absolute vibration-RMS floor for "stopped"
STOP_RMS_RATIO = 1.8     # ... or this multiple of the running quiet floor
STOP_GYRO_RMS = 0.006    # gyro magnitude RMS gate for "stopped"
STOP_MIN_DWELL_S = 2.5   # hysteresis: state must hold this long to flip

YAW_ENTER = 0.0090       # rad/s (~0.52 deg/s) -- turn begins
YAW_EXIT = 0.0045        # rad/s -- turn ends
TURN_MIN_S = 2.5         # shorter excursions are noise, folded into straight
TURN_MIN_DEG = 2.5       # ... as is anything that sweeps less than this
SEG_MIN_S = 1.0          # never emit a segment shorter than this

V_MAX = 90.0             # m/s sanity cap (324 km/h)
DT_MAX = 0.25            # clamp sample gaps (recorder hiccups)

TRACE_HZ = 5.0


def ema_alpha(dt: float, tau: float) -> float:
    return 1.0 - math.exp(-dt / tau) if tau > 0 else 1.0


# ---------------------------------------------------------------------------
# Sample source: streaming merge of accel + gyro, gyro as the clock.
# ---------------------------------------------------------------------------

def imu_stream(db_path: str):
    """Yield (t_ms, ax, ay, az, gx, gy, gz), time-ordered, O(1) memory."""
    conn = sqlite3.connect(db_path)
    acc = conn.execute(
        "SELECT epochMillis, x, y, z FROM accel_samples ORDER BY epochMillis"
    )
    gyr = conn.execute(
        "SELECT epochMillis, x, y, z FROM gyro_samples ORDER BY epochMillis"
    )
    a = acc.fetchone()
    if a is None:
        conn.close()
        return
    a_prev = a
    for g in gyr:
        t = g[0]
        # advance accel cursor to the last sample at or before t
        while a is not None and a[0] <= t:
            a_prev = a
            a = acc.fetchone()
        pick = a_prev
        if a is not None and abs(a[0] - t) < abs(a_prev[0] - t):
            pick = a
        yield (t, pick[1], pick[2], pick[3], g[1], g[2], g[3])
    conn.close()


# ---------------------------------------------------------------------------
# The online estimator.
# ---------------------------------------------------------------------------

class ShapeTracker:
    def __init__(self, leg_id: str, on_segment=None):
        self.leg_id = leg_id
        self.on_segment = on_segment      # called as each segment closes
        self.t_ms = None
        self.t_start_ms = None
        self.t_prev_ms = None

        # O1: gravity / device up-axis
        self.g = None                 # gravity vector estimate, device frame
        self.g_ref = None             # pose reference, for O2 change detection
        self.orientation_ok = True
        self.pose_changes = 0

        # vibration / stationarity
        self.a_lp = [0.0, 0.0, 0.0]   # low-pass accel, for the high-pass split
        self.vib_buf = deque()        # (t, hf_mag^2)
        self.gyr_buf = deque()        # (t, |gyro|^2)
        self.vib_sum = 0.0
        self.gyr_sum = 0.0
        self.quiet_floor = None       # running estimate of "stopped" vibration
        self.moving = False
        self.cand_moving = False
        self.cand_since_ms = None
        self.t_first_move_ms = None

        # yaw
        self.yaw_bias = 0.0           # gyro-about-up bias, learned when stopped
        self.yaw_bias_seen = 0.0
        self.yaw_rate = 0.0           # filtered, bias-corrected, + = right
        self.heading = 0.0            # integrated, rad, + = right

        # smoothed signals for the centripetal estimator
        self.a_sm = None
        self.g_sm = None

        # lateral-axis regression: hor_accel ~ b * omega, |b| = mean speed
        self.lat_num = [0.0, 0.0, 0.0]
        self.lat_den = 0.0
        self.lat = None
        self.fwd = None
        self.v_reg = 0.0

        # cant angle from integrated roll about the forward axis
        self.roll = 0.0
        self.roll_base = 0.0

        # speed (H2 prior)
        self.a_long_bias = 0.0
        self.v = 0.0
        self.v_curve = 0.0
        self.v_curve_t_ms = None
        self.curve_speed_s = 0.0
        self.moving_since_ms = None
        self.speed_source = "none"     # none | curve | regression | default

        # dead-reckoned shape
        self.x = 0.0
        self.y = 0.0
        self.dist = 0.0

        # segmentation
        self.segments = []
        self.stream_log = []
        self.cur = None               # open segment accumulator
        self.turn_state = 0           # 0 straight, +1 right, -1 left
        self.excursion_s = 0.0
        self.excursion_deg = 0.0

        self.trace = []
        self._trace_next_ms = None
        self.n_samples = 0

    # -- helpers ------------------------------------------------------------

    def _window_push(self, buf, total, t_ms, val, span_ms):
        buf.append((t_ms, val))
        total += val
        while buf and t_ms - buf[0][0] > span_ms:
            total -= buf.popleft()[1]
        return total

    # -- main --------------------------------------------------------------

    def step(self, t_ms, ax, ay, az, gx, gy, gz):
        if self.t_prev_ms is None:
            self.t_start_ms = t_ms
            self.t_prev_ms = t_ms
            self.g = [ax, ay, az]
            self.g_ref = [ax, ay, az]
            self.a_lp = [ax, ay, az]
            self._trace_next_ms = t_ms
            self._open_segment(t_ms)
            return
        dt = (t_ms - self.t_prev_ms) / 1000.0
        if dt <= 0:
            return
        dt = min(dt, DT_MAX)
        self.t_prev_ms = t_ms
        self.t_ms = t_ms
        self.n_samples += 1

        # --- O1: gravity and the up axis -----------------------------------
        ag = ema_alpha(dt, TAU_GRAVITY)
        self.g = [self.g[i] + ag * (v - self.g[i]) for i, v in enumerate((ax, ay, az))]
        gn = math.sqrt(sum(c * c for c in self.g)) or 1e-9
        up = [c / gn for c in self.g]

        # --- O2: pose change detection -------------------------------------
        rn = math.sqrt(sum(c * c for c in self.g_ref)) or 1e-9
        cos_tilt = sum(self.g[i] * self.g_ref[i] for i in range(3)) / (gn * rn)
        if cos_tilt < math.cos(math.radians(15.0)):
            self.pose_changes += 1
            self.orientation_ok = False
            self.g_ref = list(self.g)
            self.fwd = None            # forward axis is invalid after a re-pose
            self.fwd_boot = [0.0, 0.0, 0.0]
            self.fwd_boot_t = 0.0

        # --- vibration energy + stationarity (M2) --------------------------
        ah = ema_alpha(dt, TAU_HP)
        self.a_lp = [self.a_lp[i] + ah * (v - self.a_lp[i]) for i, v in enumerate((ax, ay, az))]
        hf = [ax - self.a_lp[0], ay - self.a_lp[1], az - self.a_lp[2]]
        hf2 = sum(c * c for c in hf)
        span = VIB_WINDOW_S * 1000.0
        self.vib_sum = self._window_push(self.vib_buf, self.vib_sum, t_ms, hf2, span)
        self.gyr_sum = self._window_push(
            self.gyr_buf, self.gyr_sum, t_ms, gx * gx + gy * gy + gz * gz, span
        )
        vib_rms = math.sqrt(self.vib_sum / max(len(self.vib_buf), 1))
        gyr_rms = math.sqrt(self.gyr_sum / max(len(self.gyr_buf), 1))

        # running "quiet floor": fast down, slow up -> adapts to the coach
        if self.quiet_floor is None:
            self.quiet_floor = vib_rms
        elif vib_rms < self.quiet_floor:
            self.quiet_floor += 0.25 * (vib_rms - self.quiet_floor)
        else:
            self.quiet_floor *= (1.0 + dt / 1800.0)
        stop_gate = max(STOP_RMS_FLOOR, STOP_RMS_RATIO * self.quiet_floor)

        cand = (vib_rms > stop_gate) or (gyr_rms > STOP_GYRO_RMS)
        if cand != self.cand_moving:
            self.cand_moving = cand
            self.cand_since_ms = t_ms
        elif cand != self.moving and self.cand_since_ms is not None and \
                (t_ms - self.cand_since_ms) / 1000.0 >= STOP_MIN_DWELL_S:
            self.moving = cand
            if self.moving:
                self.moving_since_ms = t_ms
                if self.t_first_move_ms is None:
                    self.t_first_move_ms = t_ms

        # --- yaw rate about the up axis (rotation-invariant in heading) -----
        yaw_raw = -(gx * up[0] + gy * up[1] + gz * up[2])   # + = right turn
        if not self.moving:
            ab = ema_alpha(dt, 10.0)
            self.yaw_bias += ab * (yaw_raw - self.yaw_bias)
            self.yaw_bias_seen += dt
        ay_ = ema_alpha(dt, TAU_YAW)
        self.yaw_rate += ay_ * ((yaw_raw - self.yaw_bias) - self.yaw_rate)

        # --- smoothed accel/gyro for the curve estimator --------------------
        asm = ema_alpha(dt, TAU_SMOOTH)
        if self.a_sm is None:
            self.a_sm, self.g_sm = [ax, ay, az], [gx, gy, gz]
        self.a_sm = [self.a_sm[i] + asm * (v - self.a_sm[i])
                     for i, v in enumerate((ax, ay, az))]
        self.g_sm = [self.g_sm[i] + asm * (v - self.g_sm[i])
                     for i, v in enumerate((gx, gy, gz))]

        dyn = [self.a_sm[i] - up[i] * 9.81 for i in range(3)]
        vert = sum(dyn[i] * up[i] for i in range(3))
        hor = [dyn[i] - vert * up[i] for i in range(3)]
        om = -(self.g_sm[0] * up[0] + self.g_sm[1] * up[1]
               + self.g_sm[2] * up[2]) - self.yaw_bias

        # --- lateral axis: regress horizontal accel on yaw rate -------------
        # In a curve hor ~ v * omega * lat_unit, so the regression coefficient
        # vector points along lateral and its length scales with mean speed.
        if abs(om) > OMEGA_SPEED_MIN / 2.0 and self.moving:
            decay = math.exp(-dt / TAU_LAT)
            self.lat_num = [self.lat_num[i] * decay + hor[i] * om * dt
                            for i in range(3)]
            self.lat_den = self.lat_den * decay + om * om * dt
            if self.lat_den > 1e-9:
                b = [n / self.lat_den for n in self.lat_num]
                bn = math.sqrt(sum(c * c for c in b))
                if bn > 1e-6:
                    self.lat = [c / bn for c in b]
                    self.v_reg = min(SPEED_SCALE * REG_ALPHA * bn, V_MAX)
                    # forward = lateral x up (right-handed, horizontal)
                    f = [self.lat[1] * up[2] - self.lat[2] * up[1],
                         self.lat[2] * up[0] - self.lat[0] * up[2],
                         self.lat[0] * up[1] - self.lat[1] * up[0]]
                    fn = math.sqrt(sum(c * c for c in f)) or 1e-9
                    self.fwd = [c / fn for c in f]

        # --- cant angle: integrated roll about forward, slow level baseline --
        if self.fwd is not None:
            rr = sum(self.g_sm[i] * self.fwd[i] for i in range(3))
            self.roll += rr * dt
            self.roll_base += ema_alpha(dt, TAU_ROLL_BASE) * (self.roll - self.roll_base)
        cant = self.roll - self.roll_base

        # --- speed from curve geometry: v = a_lat / omega --------------------
        if self.lat is not None and abs(om) > OMEGA_SPEED_MIN and self.moving:
            a_lat = sum(hor[i] * self.lat[i] for i in range(3))
            a_lat += SPEED_BETA * 9.81 * math.sin(cant) * (1.0 if om > 0 else -1.0)
            v_c = SPEED_SCALE * SPEED_ALPHA * a_lat / om
            if V_MIN_VALID <= v_c <= V_MAX:
                self.v_curve = v_c
                self.v_curve_t_ms = t_ms
                self.curve_speed_s += dt

        # --- speed prior (H2) ------------------------------------------------
        # Accel integration alone is worthless here: track grade and handling
        # noise swamp the ~0.15 m/s^2 of real train acceleration (measured).
        # So dead-reckon short-term but pull hard toward the curve estimate,
        # and zero on ZUPT.
        a_long = sum(dyn[i] * self.fwd[i] for i in range(3)) if self.fwd else 0.0
        if not self.moving:
            self.v = 0.0
            self.a_long_bias += ema_alpha(dt, 5.0) * (a_long - self.a_long_bias)
        else:
            self.v += (a_long - self.a_long_bias) * dt
            target = None
            if self.v_curve_t_ms is not None and (t_ms - self.v_curve_t_ms) < 120000:
                target, self.speed_source = self.v_curve, "curve"
            elif self.v_reg > V_MIN_VALID:
                target, self.speed_source = self.v_reg, "regression"
            elif self.moving_since_ms is not None and \
                    (t_ms - self.moving_since_ms) / 1000.0 > NO_EVIDENCE_GRACE_S:
                target, self.speed_source = DEFAULT_CRUISE_MPS, "default"
            if target is not None:
                self.v += ema_alpha(dt, TAU_SPEED_FUSE) * (target - self.v)
            self.v = max(0.0, min(self.v, V_MAX))

        # --- dead reckoning -------------------------------------------------
        self.heading += self.yaw_rate * dt
        self.x += self.v * math.sin(self.heading) * dt
        self.y += self.v * math.cos(self.heading) * dt
        self.dist += self.v * dt

        # --- turn state machine (M1) ----------------------------------------
        self._segment_step(t_ms, dt)

        # --- trace ----------------------------------------------------------
        if t_ms >= self._trace_next_ms:
            self._trace_next_ms = t_ms + int(1000.0 / TRACE_HZ)
            self.trace.append((
                t_ms, math.degrees(self.yaw_rate), math.degrees(self.heading),
                self.v, self.v_curve, 1 if self.moving else 0, vib_rms, stop_gate,
                self.x, self.y, self.dist,
            ))

    # -- segmentation ------------------------------------------------------

    def _open_segment(self, t_ms, seg_type="straight"):
        self.cur = {
            "t_start": t_ms,
            "type": seg_type,
            "turn_rad": 0.0,
            "abs_turn_rad": 0.0,
            "dur": 0.0,
            "moving_s": 0.0,
            "dist": 0.0,
            "src": {},
        }

    def _close_segment(self, t_ms, next_type):
        c = self.cur
        dur = max((t_ms - c["t_start"]) / 1000.0, 1e-6)
        prev = self.segments[-1] if self.segments else None
        too_short = dur < SEG_MIN_S
        if too_short and prev is not None:
            # fold into the previous segment rather than emit noise
            prev["t_end"] = t_ms
            prev["turn_deg"] += math.degrees(c["turn_rad"])
            prev["_dist"] += c["dist"]
            prev["_dur"] += dur
            prev["_moving_s"] += c["moving_s"]
            prev["moving"] = prev["_moving_s"] / prev["_dur"] > 0.5
            prev["speed_prior_mps"] = round(prev["_dist"] / prev["_dur"], 3)
            prev["length_prior_m"] = round(prev["_dist"], 1)
        else:
            moving = c["moving_s"] / dur > 0.5
            turn_deg = math.degrees(c["turn_rad"])
            # The integrated angle is the authoritative quantity -- the state
            # machine labels a turn from the yaw-rate excursion that triggered
            # it, which can disagree in sign or fall short once integrated.
            seg_type = c["type"]
            if seg_type != "straight":
                if abs(turn_deg) < TURN_MIN_DEG:
                    seg_type = "straight"
                else:
                    seg_type = "right" if turn_deg > 0 else "left"
            mean_rate = abs(turn_deg) / dur
            conf = min(1.0, 0.5 + mean_rate / 1.5) if seg_type != "straight" \
                else min(1.0, 0.5 + (1.5 - min(mean_rate, 1.5)) / 3.0)
            if not self.orientation_ok:
                conf *= 0.5
            seg = {
                "seg_id": len(self.segments),
                "type": seg_type,
                "t_start": c["t_start"],
                "t_end": t_ms,
                "turn_deg": round(turn_deg, 2),
                "moving": moving,
                "speed_prior_mps": round(c["dist"] / dur, 3),
                "length_prior_m": round(c["dist"], 1),
                "confidence": round(conf, 3),
                "speed_source": (max(c["src"], key=c["src"].get) if c["src"] else "none"),
                "_dur": dur,
                "_dist": c["dist"],
                "_moving_s": c["moving_s"],
            }
            self.segments.append(seg)
            if self.on_segment is not None:
                self.on_segment(seg, self)
            self.stream_log.append({
                "seg_id": seg["seg_id"],
                "type": seg["type"],
                "emitted_at_t": t_ms,
                "leg_elapsed_s": round((t_ms - self.t_start_ms) / 1000.0, 2),
                "latency_s": 0.0,   # closed on the sample that ended it
            })
        self._open_segment(t_ms, next_type)

    def _segment_step(self, t_ms, dt):
        c = self.cur
        c["dur"] += dt
        c["dist"] += self.v * dt
        if self.moving:
            c["moving_s"] += dt
        c["turn_rad"] += self.yaw_rate * dt
        if self.moving:
            c["src"][self.speed_source] = c["src"].get(self.speed_source, 0.0) + dt

        r = self.yaw_rate
        want = 0
        if abs(r) > YAW_ENTER:
            want = 1 if r > 0 else -1

        if self.turn_state == 0:
            if want != 0:
                self.excursion_s += dt
                self.excursion_deg += math.degrees(abs(r)) * dt
                if self.excursion_s >= TURN_MIN_S and self.excursion_deg >= TURN_MIN_DEG:
                    # retro-open the turn at (approximately) its onset: close the
                    # straight segment at the point the excursion began. Causal --
                    # it uses only already-consumed samples.
                    onset = t_ms - int(self.excursion_s * 1000.0)
                    onset = max(onset, c["t_start"])
                    frac = (onset - c["t_start"]) / max(t_ms - c["t_start"], 1)
                    carry = {
                        "turn_rad": c["turn_rad"] * (1 - frac),
                        "dist": c["dist"] * (1 - frac),
                        "moving_s": c["moving_s"] * (1 - frac),
                        "dur": c["dur"] * (1 - frac),
                    }
                    c["turn_rad"] *= frac
                    c["dist"] *= frac
                    c["moving_s"] *= frac
                    c["dur"] *= frac
                    self._close_segment(onset, "right" if want > 0 else "left")
                    self.cur.update(carry)
                    self.turn_state = want
                    self.excursion_s = 0.0
                    self.excursion_deg = 0.0
            else:
                self.excursion_s = 0.0
                self.excursion_deg = 0.0
        else:
            same_sign = (r > 0) == (self.turn_state > 0)
            if abs(r) < YAW_EXIT or not same_sign:
                self.excursion_s += dt
                if self.excursion_s >= TURN_MIN_S:
                    self._close_segment(t_ms, "straight")
                    self.turn_state = 0
                    self.excursion_s = 0.0
                    self.excursion_deg = 0.0
            else:
                self.excursion_s = 0.0

        # a stop/start boundary is a segment boundary too (H3 needs it split)
        if self.cur["dur"] > SEG_MIN_S:
            frac_moving = self.cur["moving_s"] / self.cur["dur"]
            if 0.2 < frac_moving < 0.8 and self.cur["dur"] > 2 * SEG_MIN_S:
                self._close_segment(t_ms, self.cur["type"])

    # -- output ------------------------------------------------------------

    def finish(self):
        if self.cur is not None and self.t_ms is not None:
            self._close_segment(self.t_ms, "straight")
            self.cur = None
        for s in self.segments:
            for k in ("_dur", "_dist", "_moving_s"):
                s.pop(k, None)
        return {
            "leg_id": self.leg_id,
            "t_start": self.t_start_ms,
            "t_end": self.t_ms,
            "orientation_ok": self.orientation_ok,
            "segments": self.segments,
            "diagnostics": {
                "n_samples": self.n_samples,
                "pose_changes": self.pose_changes,
                "gyro_bias_dps": round(math.degrees(self.yaw_bias), 4),
                "gyro_bias_learn_s": round(self.yaw_bias_seen, 1),
                "curve_speed_evidence_s": round(self.curve_speed_s, 1),
                "regression_mean_speed_mps": round(self.v_reg, 2),
                "total_heading_deg": round(math.degrees(self.heading), 1),
                "dead_reckoned_length_m": round(self.dist, 1),
                "forward_axis_found": self.fwd is not None,
            },
        }


# ---------------------------------------------------------------------------
# Rendering / IO
# ---------------------------------------------------------------------------

def dr_polyline(trace, lat0, lon0, bearing_deg):
    """Dead-reckoned x/y (metres, heading-relative) -> lat/lon polyline.

    Needs a start position *and* a start bearing, so it is only available on
    the warm track where those are given. Purely for looking at -- the shape
    itself carries no absolute position.
    """
    b = math.radians(bearing_deg)
    mlon = 111320.0 * math.cos(math.radians(lat0)) or 1e-9
    out = []
    for r in trace:
        x, y = r[8], r[9]
        east = x * math.cos(b) + y * math.sin(b)
        north = -x * math.sin(b) + y * math.cos(b)
        out.append({"t": r[0], "lat": lat0 + north / 110540.0,
                    "lon": lon0 + east / mlon, "distance_m": round(r[10], 1)})
    return out


def write_svg(path, trace, leg_id, shape):
    pts = [(r[8], r[9]) for r in trace]
    if len(pts) < 2:
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w = h = 900.0
    pad = 40.0
    sx = (maxx - minx) or 1.0
    sy = (maxy - miny) or 1.0
    s = min((w - 2 * pad) / sx, (h - 2 * pad) / sy)

    def px(p):
        return (pad + (p[0] - minx) * s, h - pad - (p[1] - miny) * s)

    d = " ".join(
        ("M" if i == 0 else "L") + "%.1f,%.1f" % px(p) for i, p in enumerate(pts)
    )
    color = {"left": "#2a7", "right": "#c52", "straight": "#579"}
    marks = []
    tmap = {r[0]: (r[8], r[9]) for r in trace}
    keys = sorted(tmap)
    for seg in shape["segments"]:
        if seg["type"] == "straight":
            continue
        k = min(keys, key=lambda t: abs(t - seg["t_start"]))
        cx, cy = px(tmap[k])
        marks.append(
            '<circle cx="%.1f" cy="%.1f" r="4" fill="%s"/>' % (cx, cy, color[seg["type"]])
        )
    head = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d">' % (w, h, w, h)
    )
    legend = (
        '<text x="12" y="24" font-family="monospace" font-size="14">%s | %.1f km DR | '
        '%d segs | net heading %.0f deg</text>'
        % (leg_id, shape["diagnostics"]["dead_reckoned_length_m"] / 1000.0,
           len(shape["segments"]), shape["diagnostics"]["total_heading_deg"])
    )
    with open(path, "w") as f:
        f.write(head)
        f.write('<rect width="100%" height="100%" fill="#fff"/>')
        f.write('<path d="%s" fill="none" stroke="#123" stroke-width="2"/>' % d)
        f.write("".join(marks))
        f.write(legend)
        f.write("</svg>")


def write_trace(path, trace):
    with open(path, "w") as f:
        f.write("epochMillis,yawRateDps,headingDeg,speedMps,speedCurveMps,moving,"
                "vibRms,stopGate,x_m,y_m,distM\n")
        for r in trace:
            f.write("%d,%.4f,%.3f,%.3f,%.3f,%d,%.4f,%.4f,%.2f,%.2f,%.2f\n" % r)


def run_leg(root, leg_dir, out_root, want_svg=True, want_stream=False,
            run=None, warm_start=None):
    """Solve one leg. `run` is an optional rail_gui.RunWriter for the viewer."""
    db = os.path.join(root, leg_dir, "sensors.db")
    if not os.path.exists(db):
        raise SystemExit("no sensors.db at %s" % db)

    gui = run.leg(leg_dir) if run is not None else None
    if gui is not None:
        gui.event("I1", "streaming accel+gyro from sensors.db", pct=0.0)

    # Leg duration is read up front for the progress bar only -- the algorithm
    # itself never sees it (it has to work on a live stream).
    t_lo = t_hi = None
    if gui is not None:
        try:
            conn = sqlite3.connect(db)
            t_lo, t_hi = conn.execute(
                "SELECT MIN(epochMillis), MAX(epochMillis) FROM gyro_samples").fetchone()
            conn.close()
        except sqlite3.Error:
            t_lo = t_hi = None

    state = {"last_pct": -1.0}

    def on_segment(seg, tk):
        if gui is None:
            return
        pct = None
        if t_lo and t_hi and t_hi > t_lo:
            pct = min(0.99, (seg["t_end"] - t_lo) / float(t_hi - t_lo))
            if pct - state["last_pct"] < 0.02:
                pct = None
            else:
                state["last_pct"] = pct
        stage = "M1" if seg["type"] != "straight" else "M2"
        msg = "seg %d %s %+.1f deg, %.0f s, %s" % (
            seg["seg_id"], seg["type"], seg["turn_deg"],
            (seg["t_end"] - seg["t_start"]) / 1000.0,
            "moving" if seg["moving"] else "stopped")
        gui.event(stage, msg, pct=pct)

    tk = ShapeTracker(leg_dir, on_segment=on_segment)
    t0 = time.time()
    for row in imu_stream(db):
        tk.step(*row)
    shape = tk.finish()
    elapsed = time.time() - t0

    out = os.path.join(out_root, leg_dir)
    os.makedirs(out, exist_ok=True)
    # the lane contract lives in work/<leg_id>/; the viewer reads the run dir
    targets = [out] + ([str(gui.dir)] if gui is not None else [])
    for tgt in targets:
        with open(os.path.join(tgt, "shape.json"), "w") as f:
            json.dump(shape, f, indent=2)
        write_trace(os.path.join(tgt, "shape_trace.csv"), tk.trace)
        if want_svg:
            write_svg(os.path.join(tgt, "shape.svg"), tk.trace, leg_dir, shape)
        if want_stream:
            with open(os.path.join(tgt, "shape_stream.jsonl"), "w") as f:
                for e in tk.stream_log:
                    f.write(json.dumps(e) + "\n")

    d = shape["diagnostics"]
    turns = [s for s in shape["segments"] if s["type"] != "straight"]
    dur = (shape["t_end"] - shape["t_start"]) / 1000.0
    if gui is not None:
        if warm_start is not None:
            gui.hydrated(dr_polyline(tk.trace, *warm_start),
                         source="dead_reckoning_only",
                         note="motion lane only -- no anchors, no scale correction")
        if not shape["orientation_ok"]:
            gui.event("O2", "pose changed mid-leg -- shape is suspect", level="warn")
        if d["curve_speed_evidence_s"] < 30.0:
            gui.event("H2", "under 30 s of curve evidence -- length prior is a guess",
                      level="warn")
        gui.done("%d segs, %d turns, %.1f km dead-reckoned, net %+.0f deg"
                 % (len(shape["segments"]), len(turns),
                    d["dead_reckoned_length_m"] / 1000.0, d["total_heading_deg"]))

    print("%-52s %6.0fs  segs=%-3d turns=%-3d L=%.2fkm  hdg=%+.0f  "
          "ori_ok=%s  v_reg=%.1f  (%.1fs cpu, %.0fx realtime)"
          % (leg_dir, dur, len(shape["segments"]), len(turns),
             d["dead_reckoned_length_m"] / 1000.0, d["total_heading_deg"],
             shape["orientation_ok"], d["regression_mean_speed_mps"], elapsed,
             dur / max(elapsed, 1e-6)))
    return shape


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    ap = argparse.ArgumentParser()
    ap.add_argument("--practice-dir", default=os.path.join(repo, "datasets", "practice"))
    ap.add_argument("--out-dir", default=os.path.join(repo, "work"))
    ap.add_argument("--leg", help="leg directory name (prefix match is fine)")
    ap.add_argument("--legs", nargs="*", metavar="LEG",
                    help="several leg directory names (prefix match each)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-svg", action="store_true")
    ap.add_argument("--stream-log", action="store_true",
                    help="also write shape_stream.jsonl (segment emission order)")
    ap.add_argument("--no-gui", action="store_true",
                    help="skip work/runs/<run_id>/ (the web/ viewer's run dir)")
    ap.add_argument("--run-id", help="run directory name (default: timestamped)")
    ap.add_argument("--notes", help="free text shown in the viewer's run list")
    ap.add_argument("--warm-start", metavar="LAT,LON,BEARING",
                    help="given start pose (warm track). Only used to draw the "
                         "dead-reckoned path on the viewer's map -- never fed "
                         "back into the algorithm.")
    a = ap.parse_args()

    warm = None
    if a.warm_start:
        try:
            lat, lon, brg = (float(v) for v in a.warm_start.split(","))
            warm = (lat, lon, brg)
        except ValueError:
            raise SystemExit("--warm-start wants LAT,LON,BEARING")

    legs = sorted(d for d in os.listdir(a.practice_dir)
                  if os.path.isdir(os.path.join(a.practice_dir, d)))
    wanted = list(a.legs or [])
    if a.leg:
        wanted.append(a.leg)
    if wanted:
        # Keep the caller's order, one match per name, no duplicates.
        picked = []
        for name in wanted:
            hit = next((d for d in legs if d == name or d.startswith(name)), None)
            if hit is None:
                raise SystemExit("no leg matching %r" % name)
            if hit not in picked:
                picked.append(hit)
        legs = picked
    elif not a.all:
        legs = legs[:1]
    if a.limit:
        legs = legs[: a.limit]

    run = None
    if not a.no_gui:
        if RunWriter is None:
            print("rail_gui unavailable -- running without the viewer",
                  file=sys.stderr)
        else:
            run = RunWriter("motion-lane", lane="motion", run_id=a.run_id,
                            notes=a.notes or "shape_stream.py: O1/O2/M1/M2/M3 + H2 prior")
    try:
        for d in legs:
            run_leg(a.practice_dir, d, a.out_dir, not a.no_svg, a.stream_log,
                    run=run, warm_start=warm)
    except BaseException:
        if run is not None:
            run.finish("error")
        raise
    if run is not None:
        run.finish("done")
        print("run dir: %s" % run.dir, file=sys.stderr)
    print("wrote %d leg(s) to %s" % (len(legs), a.out_dir), file=sys.stderr)


if __name__ == "__main__":
    main()
