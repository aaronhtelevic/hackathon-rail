---
name: worklog
description: Review WORKLOG.md before ingesting new data, docs, or ideas into this repo, and update it after. Use whenever exploring datasets, adding reference material, or proposing an approach — prevents duplicate work and repeating things already tried and rejected. Triggers: "ingest", "add data", "new approach", "explore dataset", "log this", "check worklog".
---

# Worklog discipline

This repo keeps a living approach doc at `WORKLOG.md` (repo root). It's the
current best understanding of how to solve the problem — not a chronicle of
what happened when. It exists so ideas, data explorations, and approaches
aren't repeated or re-proposed after already being tried (and possibly
rejected).

## Before ingesting anything new

"Ingesting" = reading a new dataset/file for the first time, adding new
reference data, proposing a modeling/localization approach, or starting a
new investigation.

1. Read `WORKLOG.md` in full first.
2. Check whether the thing you're about to do — or something close to it —
   is already covered:
   - Already done and documented → reuse the existing finding, don't redo it.
   - Already tried and marked as not working → don't propose it again unless
     the user explicitly asks to revisit it, and note *why* it failed before
     trying a variant.
   - Not present → proceed.

## After finishing a unit of work

Update `WORKLOG.md` in place — merge into the relevant section rather than
appending a new dated entry. Sections:

- **Setup** — one-off environment/data-inspection notes. Rarely changes.
- **Constraints** — hard rules discovered (e.g. what data can't be used).
- **Algorithm** — the current approach, as numbered steps/stages. When a
  step changes, is validated, or is rejected, edit that step in place:
  - Validated → tighten the wording, note briefly what confirmed it.
  - Rejected → mark it rejected with why, don't delete (prevents re-proposing).
  - Superseded → replace the step, keep a short note on what didn't work.

Keep it factual and short — this is a working spec, not a report. Only
record what's non-obvious from reading the code/data itself (decisions,
dead ends, why something was rejected) — don't restate things
`DATA_SCHEMA.md` or the codebase already show.

## Scope

Don't log routine reads with no decision attached (e.g. just opening a file
to check something). Do capture: new data sources added, approaches tried
for localization/route-matching/station-detection, things that turned out
wrong or misleading, and any assumption that later proved false.
