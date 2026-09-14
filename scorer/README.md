# Scorer

Run the same scoring logic the organizers use, against your own practice-set
submissions.

```
python scorer.py --leg-id <leg_id> --track cold|warm --submission-dir <path-to-your-output>
```

`<leg_id>` is one of the folder names under `../datasets/practice/` (e.g.
`ic830_00_kortrijk_ingelmunster`). `--submission-dir` needs a `position.csv`
(and optionally `station_calls.csv`) in the same format described in
`../datasets/README.md` — see `../starter_kit/` for a fully worked example.

Prints a JSON report with all 6 metrics (see `../datasets/PARTICIPANT_BRIEF.md`
for what each one means). Pass `--json-out results.json` to also save it.

## What this can and can't do

This package only ever reads `../datasets/practice/` — the one place a real
answer key (`ground_truth.csv`) actually sits on your machine. It has no
access to the blind or scoring sets' answers (organizers keep those), so
there's no way to point this at a leg you're actually being judged on and
get real feedback — it'll just fail with a clear error telling you that leg
isn't a practice leg.

**A heads-up worth taking seriously:** running this against the 50 practice
legs is exactly what it's for — sanity-check your pipeline, calibrate your
approach, catch bugs. But it's easy to unconsciously over-tune to quirks of
these specific 50 legs in a way that doesn't generalize to the legs you're
actually scored on later. A great practice-set number is informative, not a
guarantee — the real test is the blind/scoring legs, where this scorer can't
help you (by design).
