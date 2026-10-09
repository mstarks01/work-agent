# 75. The assertion pass runs by default

- **Status**: accepted
- **Date**: 2026-10-09
- **Effort**: [#1231](https://github.com/mstarks01/work-agent/issues/1231)
- **Relates to**: [ADR 0036](0036-a-settled-assertion-is-evidence-a-lane-may-cite.md),
  which makes a settled row evidence that a lane may cite, and
  [ADR 0042](0042-every-predicate-states-its-reader.md), whose rules read the
  catalog the pass builds
- **Evidence**: `QA-2026-10-09-03-E1` to `E3` in `evals/experiments/`; the
  #926 A/B for the cost

## Context

The assertion pass reads the sources after the validity gate and records each
fact they state, with the quote that supports it. The lanes can cite these
rows, the rules that read the catalog fire on them, a paused job can ask which
element each principal is, and the report carries the rows. Without the pass,
none of this happens, because the system model has no field for most of these
facts.

The pass costs about $0.024 and 155 s for each job (#926). Its effect on
findings is small and not separated from chance. With the signed facts as the
catalog, which is the most a pass can write:

- a catalog rule leads to 1 of 103 STRIDE must-finds that no rule leads to
  without it, and to no ASVS must-find (`E1`);
- on the 25 must-finds that the lanes missed in two archived runs, the lanes
  matched 3 with no catalog and 4 and 5 in two passes with the catalog (`E3`).

The promotion gates in `evals/harness/promotion.py` state what a measured
promotion needs. Their quality gates are not read.

## Decision

**The assertion pass runs on every job by default** (maintainer's decision of
2026-10-09). `ANALYSIS_ASSERTIONS` set to a value that is not an affirmative
turns it off. A deployment that selects a facts-first reading runs no pass,
because that reading writes its own rows.

The decision does not rest on the promotion gates, and it does not pass them.
They stay as the record of what a measured promotion needs.

## Consequences

- Every job pays one more `base`-tier call and waits for it.
- A paused job asks link questions by default, because a deployment now carries
  a catalog.
- An eval sweep runs the pass unless its environment turns it off. The
  archived Baselines ran without it, so a run compared with one of them sets
  `ANALYSIS_ASSERTIONS=false`.
- The scripted test environment (`tests/factories.TEST_TIER_ENV`) turns the
  pass off, because it scripts no `assert` node. A test of the pass turns it on.
