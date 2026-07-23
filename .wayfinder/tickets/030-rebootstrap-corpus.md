---
id: 030
title: "Re-bootstrap the phase-1 corpus against the real extract node"
label: wayfinder:task
status: closed
assignee:
blocked-by: [027]
---

## Question

[Author the phase-1 golden corpus](022-author-golden-corpus.md) shipped with one
declared departure from decision 2: with no Vertex credentials available, each
phase-1 candidate model came from an **agent stand-in** running
`prompts/extract.md` rather than the pinned Flash node. Every `case.json` records
this honestly as `"bootstrap": "agent-stand-in"`.

The blessed models are unaffected — they are blessed against the source text — so
nothing in the corpus is wrong. What is wrong is the **meaning of the diffs**:
the 37 recorded corrections in `corrections.md` are currently signal about *the
prompt*, not about the pinned model's own blind spots, which is what decision 2
asked them to be. That distinction matters because the accumulated "what
extraction habitually gets wrong" record is what weights the extraction eval.

Re-run `extract` over each `source.md` with the real pinned Flash node, re-record
the diff against the blessed model, and flip the provenance field. Cheap once
credentials exist.

Two things to watch:

- The existing taxonomy is a **prediction to test**, not a baseline to preserve.
  The most repeated phase-1 failure was a stated qualifier stranded in
  `source_excerpt` and never reaching an attribute analysts read; the most
  alarming was a single **invented absence** — a control asserted missing rather
  than `unknown`. Whether the real Flash node reproduces either is the actual
  finding here.
- If this runs after [Expand the golden corpus to 12](029-expand-golden-corpus.md),
  bootstrap the new cases the same way so the whole corpus carries one
  provenance, rather than two generations of it.

## Closed — out of scope (2026-07-23)

Not resolved on the route — **closed out of scope**. This ticket needs a live
Vertex call (sweeps, calibration runs, or re-bootstrapping against the real
model) to produce its numbers. The user ruled GCP/Vertex provisioning and all
live eval measurement out of scope — the app assumes a correctly configured
environment — which closed [Provision Vertex access](027-provision-live-infrastructure.md)
and, with it, the only path to those numbers here. The offline machinery ships;
the numbers, the >=90% judge bar, and the Tier-2/3 gate promotions are a future
eval effort, not a ticket in this map. See the map's Out of scope section.
