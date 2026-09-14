---
name: worklog
description: Review WORKLOG.md before ingesting new data, docs, or ideas into this repo, and log the result after. Use whenever exploring datasets, adding reference material, or proposing an approach — prevents duplicate work and repeating things already tried and rejected. Triggers: "ingest", "add data", "new approach", "explore dataset", "log this", "check worklog".
---

# Worklog discipline

This repo keeps a running memory file at `WORKLOG.md` (repo root). It exists
so ideas, data explorations, and approaches aren't repeated or re-proposed
after already being tried (and possibly rejected) earlier in the hackathon.

## Before ingesting anything new

"Ingesting" = reading a new dataset/file for the first time, adding new
reference data, proposing a modeling/localization approach, or starting a
new investigation.

1. Read `WORKLOG.md` in full first.
2. Check whether the thing you're about to do — or something close to it —
   is already logged:
   - Already done and documented → reuse the existing finding, don't redo it.
   - Already tried and marked as not working → don't propose it again unless
     the user explicitly asks to revisit it, and note *why* it failed before
     trying a variant.
   - Not present → proceed.

## After finishing a unit of work

Append an entry to `WORKLOG.md` under `## Log`, newest last, dated
`YYYY-MM-DD`:

```markdown
### YYYY-MM-DD — <short title>
- What was done (files touched, data inspected, commands run).
- Outcome: what worked, or what was tried and didn't work / was rejected, and why.
```

Keep entries factual and short — this is a log, not a report. Only record
what's non-obvious from reading the code/data itself (decisions, dead ends,
why something was rejected) — don't restate things `DATA_SCHEMA.md` or the
codebase already show.

## Scope

Don't log routine reads with no decision attached (e.g. just opening a file
to check something). Do log: new data sources added, approaches tried for
localization/route-matching/station-detection, things that turned out wrong
or misleading, and any assumption that later proved false.
