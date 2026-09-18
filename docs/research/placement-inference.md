# What extraction places a component on

Frozen evidence for [#1052](https://github.com/mstarks01/work-agent/issues/1052)
and PRs [#1067](https://github.com/mstarks01/work-agent/pull/1067) and
[#1068](https://github.com/mstarks01/work-agent/pull/1068), measured 2026-09-18.

Read it with `docs/research/probe_placements.py` over
`evals/emissions/20260918T-placement-rule/`. Both sides replay through today's
gate with nothing refused, so a later scorer re-applies them at no cost.

## What a placement is scored against

Each case's `facts.json` carries a signed `network-membership` row per
component. The row's `basis` decides what the model owes it:

- **`stated`** with a zone — the sources name where it runs. Declining it is a
  real loss.
- **`inferred`** with a zone — the reviewer inferred it because the old schema
  demanded one. Declining it is what ADR 0039 rule 1 asks for.
- **`unknown`** — the sources place it nowhere. Naming a zone is an invention.

Scoring by the value alone reports the endorsed inferences as regressions. It
made case 01's `entity:shopper` read as a defect when it was the contract
working.

## The three patterns

ADR 0039 freed the field, and five sweeps then measured 23.6 honest absences a
run against **16.8 placements the model still invented**. Reading all 84 of
them — 31 distinct components — found three patterns, each one a placement drawn
from something that is not a sentence about where the component runs.

**Its own identity.** A boundary created to hold one component and named after
it: `boundary:cdn` for the CDN, `boundary:scheduling-partner` for the partner,
`boundary:sokify` for four Sokify services. Placement by tautology.

**What reaches it.** Case 01 says the storefront API "is the only thing we
expose to the internet". All five runs put it in `boundary:internet`. Exposure
answers who can reach a process; `trust_zone` answers where it runs. The
inference gateway and the device gateway went the same way.

**What it talks to or sits on.** Case 01 places `orders-db` in the core network
because the order service reading it is there. Case 13 places the schedule
archive in the corporate network because the text calls it a folder on the
corporate file store, which names a store and not a network. Case 08 places
three stores on the same reasoning.

## What the rule bought

One rule naming the three patterns, five sweeps each side, same corpus and same
blessed models:

| | before | after | separation |
|---|---:|---:|---|
| invented placements | 16.8 (sd 1.30) | **4.4** (sd 2.07) | 7.2 sd |
| honest absences | 23.6 (sd 1.34) | **35.6** (sd 3.05) | 5.1 sd |
| stated zones lost | 0.2 (sd 0.45) | 0.4 (sd 0.55) | 0.4 sd |
| placements kept | 27.2 (sd 2.28) | 26.2 (sd 1.92) | 0.5 sd |
| structural failures over five sweeps | 3 | **0** | |

Both cost columns sit inside the run-to-run spread. The two stated zones lost
were one element each, in one run each, in different cases.

A 13-case extraction sweep costs $0.029 to $0.039, so the whole comparison was
$0.32. The offline half is free: `run.py oracle` reports a perfect reading
keeping 122 of 122 required rows and inventing 0 placements, against 121 and 46
before ADR 0039.

## What is left

About 4.4 invented placements a run. They share no obvious pattern, and the
return on another rule is far below the twelve this one bought.
