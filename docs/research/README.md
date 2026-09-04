# Research records

Evidence for decisions taken elsewhere. Each file answers one ticket's question,
records what a probe returned, and rules nothing.

**Read these as evidence, not as current behaviour.** Each file is frozen at its
date. Where one describes a dependency, check the pin it was probed against
before you rely on it — `pyproject.toml` holds what this repo runs today.

**Do not edit a file here to bring it up to date.** Its value is being the
record that a decision cites. Supersede it with a new file and a new ticket. The
`probe_*.py` scripts are excluded from `ruff` in `pyproject.toml` for the
same reason, so a lint cannot rewrite an artifact an ADR points at.

## What is here

| File | Ticket | Date | Probed against |
| --- | --- | --- | --- |
| `adk-nongemini-adapters.md` | #4 | 2026-07-26 | `google-adk==2.5.0` |
| `litellm-sole-adapter.md` | #8 | 2026-07-27 | `google-adk==2.5.0`, `litellm==1.93.0` |
| `vendor-sampling-support.md` | #12 | 2026-07-27 | `google-adk==2.5.0` |
| `litellm-buildtime-gate.md` + `probe_litellm_buildtime_gate.py` | #13 | 2026-07-28 | `litellm==1.93.0` |
| `litellm-reasoning-surface.md` + two `probe_litellm_reasoning_*.py` | #15 | 2026-07-28 | `litellm==1.93.0` |
| `transcript-exports.md` | #51 | 2026-07-31 | — |
| `asvs-representation.md` + `asvs-l1-subjects.csv` | #160 | 2026-08-12 | — |
| `system-model-evolution.md` + `probe_model_vocabulary.py` | #483 | 2026-09-04 | repo `8729415`, CycloneDX 1.7 |

## The one staleness you must know about

The four LiteLLM files ran against `litellm==1.93.0`. This repo pins
`litellm==1.97.0`, and one difference between those two is recorded in
`pyproject.toml`: 1.93.0 picked the native `response_format` path from a
hardcoded set of model-name substrings, and 1.97.0 reads a model-map capability
flag instead. That is why `claude-opus-5` is selectable at all.

So treat any structured-output finding in those files as a 1.93.0 observation.
The findings those files were written to settle — the served-build readback, the
fail-closed `drop_params`, and `get_optional_params` as the gate — are not
affected by that change, and `tests/test_model_gate.py` re-probes the gate
against whatever `litellm` the lockfile installs.

## Why these files are on `main`

[ADR 0015](../adr/0015-adk-and-litellm-are-one-substrate.md) accepts
`google-adk` and `litellm` as one substrate that this service cannot swap, and
cites the first five files as the reason. Until 2026-08-21 none of them was
reachable from `origin`: the commits existed in one working copy, on no branch.
A hard pin whose argument lives in one clone is a pin nobody can re-examine.
