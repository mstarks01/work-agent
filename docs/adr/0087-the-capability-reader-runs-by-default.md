# 87. The capability reader runs by default

- **Status**: accepted
- **Date**: 2026-10-14
- **Amends**: [ADR 0086](0086-a-separate-reader-states-the-capabilities-a-source-states.md),
  which shipped the reader off and declared the gate that turns it on
- **Evidence**: `QA-2026-10-14-02-E1` in `evals/experiments/`, and
  `tests/test_deployment.py`

## Context

ADR 0086 shipped the capability reader off, and declared before any run that
its default turns on only if at least 95% of the statements it makes on the
two holdout cases, as the maintainer rules them, are right.

## Decision

**The gate passed, so the reader runs by default.** One gate run on each
holdout case kept 35 and 29 statements. The maintainer ruled 63 right and 1
wrong, a precision of 98.4% (`QA-2026-10-14-02-E1`). The wrong one is
`multi-factor-authentication` read from a one-time code that is not a second
factor.

**What the gate measured.** The gate script
(`evals/experiments/QA-2026-10-14-02/gate.py`) used the node's own prompt
(`compose_capabilities_prompt`), the sources as `render_sources` renders them,
and the node's own check (`with_stated_capabilities`). It made one call to
`gpt-5.6-luna` on OpenAI for each case.

**What the gate did not measure.** The gate script did not run the node as a
job runs it. It differs from a job in three ways:

- It asked for a JSON object (`response_format={"type": "json_object"}`). A
  job sends the node's `CapabilityReading` schema to the provider.
- It sent the whole prompt as one user message. A job sends the prompt as the
  node's instruction, which reaches the provider as a system message.
- It ran the check over the case's reference `model.json`, which states no
  capability. A job runs the check over its Valid System Model after the
  capabilities that closed fields prove are written (ADR 0085), and the check
  drops a reader row that names one of those capabilities. So the 64 ruled
  statements can include statements that a job does not keep.

The precision of the node as a job runs it, and on other models, is
unmeasured.

`ANALYSIS_CAPABILITY_READER=false` turns the reader off. An unset or empty
variable keeps it on, and a non-empty value other than an affirmative turns it
off, as for the assertion pass.

## Consequences

A job whose selected framework reads capabilities makes one more `base`-tier
call, about $0.004, and the passes after the gate wait for it. Its early
capability questions and the units decided before its lanes run read the
capabilities the sources state.

The run spent the last read of both holdout cases, so the ledger counts them as
development cases. No case is left that an audit has not read; a later
measurement on unseen text needs a new holdout case.
