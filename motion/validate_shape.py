#!/usr/bin/env python3
"""DEV-ONLY validation of shape.json against meta.json / ground_truth.csv.

Reads labels, so it is a measurement tool, never part of the pipeline. Never
import this from the solver.

  python3 motion/validate_shape.py            # all legs already in work/
"""
import csv
import json
import math
import os
import sys

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
work = os.path.join(repo, "work")
practice = os.path.join(repo, "datasets", "practice")


def gt_speed_series(path):
    rows = list(csv.DictReader(open(path)))
    out = {}
    for i in range(1, len(rows)):
        t0, t1 = int(rows[i - 1]["epochMillis"]), int(rows[i]["epochMillis"])
        if 0 < t1 - t0 <= 3000:
            d = float(rows[i]["distanceAlongTrackM"]) - float(rows[i - 1]["distanceAlongTrackM"])
            out[t1 // 1000] = d / ((t1 - t0) / 1000.0)
    return out


def net_heading_from_polyline(path):
    """Net bearing change of the true route, in degrees.

    Reversals >90 deg are polyline *ordering* artifacts (the geometry is
    assembled from OSM ways, so the last chunk sometimes runs backwards), not
    real turns -- skip them or they dominate the sum.
    """
    pts = json.load(open(path))["polyline"]
    step = max(1, len(pts) // 60)
    pts = pts[::step]
    net, prev = 0.0, None
    for i in range(1, len(pts)):
        lon1, lat1 = pts[i - 1]
        lon2, lat2 = pts[i]
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dl = math.radians(lon2 - lon1)
        b = math.degrees(math.atan2(
            math.sin(dl) * math.cos(p2),
            math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)))
        if prev is not None:
            dd = (b - prev + 180) % 360 - 180
            if abs(dd) <= 90:
                net += dd
        prev = b
    return net


def main():
    legs = sorted(d for d in os.listdir(work)
                  if os.path.exists(os.path.join(work, d, "shape.json")))
    print("%-52s %8s %8s %6s  %6s %6s %7s %7s %5s" %
          ("leg", "trueKm", "drKm", "err%", "spdMed", "curveS", "hdgTrue",
           "hdgEst", "qual"))
    errs = []
    errs_ev = []
    hdg_errs = []
    for d in legs:
        shape = json.load(open(os.path.join(work, d, "shape.json")))
        meta = json.load(open(os.path.join(practice, d, "meta.json")))
        true_m = meta["routeLengthM"]
        dr = shape["diagnostics"]["dead_reckoned_length_m"]
        err = 100.0 * (dr - true_m) / true_m
        qual = meta.get("groundTruthQuality", "?")
        smed = sp90 = float("nan")
        gtp = os.path.join(practice, d, "ground_truth.csv")
        tracep = os.path.join(work, d, "shape_trace.csv")
        if os.path.exists(gtp) and os.path.exists(tracep):
            gts = gt_speed_series(gtp)
            e = []
            for r in csv.DictReader(open(tracep)):
                s = int(r["epochMillis"]) // 1000
                if s in gts and gts[s] > 2.0:
                    e.append(abs(float(r["speedMps"]) - gts[s]))
            if e:
                e.sort()
                smed, sp90 = e[len(e) // 2], e[int(len(e) * 0.9)]
        ev = shape["diagnostics"].get("curve_speed_evidence_s", 0.0)
        if qual == "good":
            errs.append(abs(err))
            if ev >= 60.0:
                errs_ev.append(abs(err))
        hdg_true = hdg_est = float("nan")
        plp = os.path.join(repo, "scorer", "polylines", d + ".json")
        if os.path.exists(plp):
            hdg_true = net_heading_from_polyline(plp)
            hdg_est = shape["diagnostics"]["total_heading_deg"]
            if qual == "good":
                hdg_errs.append(abs(hdg_est - hdg_true))
        print("%-52s %8.2f %8.2f %+6.1f  %6.2f %6.0f %+7.0f %+7.0f %5s" %
              (d, true_m / 1000.0, dr / 1000.0, err, smed, ev, hdg_true,
               hdg_est, qual))
    if errs:
        errs.sort()
        print("\ngood legs: median |length err| %.1f%%  p90 %.1f%%  (n=%d)" %
              (errs[len(errs) // 2], errs[int(len(errs) * 0.9)], len(errs)))
    if hdg_errs:
        hdg_errs.sort()
        print("good legs: median |net heading err| %.0f deg  p90 %.0f deg  (n=%d)" %
              (hdg_errs[len(hdg_errs) // 2], hdg_errs[int(len(hdg_errs) * 0.9)],
               len(hdg_errs)))
    if errs_ev:
        errs_ev.sort()
        print("good legs with >=60 s of curve evidence: median %.1f%%  p90 %.1f%%  (n=%d)" %
              (errs_ev[len(errs_ev) // 2], errs_ev[int(len(errs_ev) * 0.9)], len(errs_ev)))


if __name__ == "__main__":
    main()
