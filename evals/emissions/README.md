# Archived emissions

What `extract` and `assert` said in sweeps this repository paid for, kept so
that a scorer, a normalizer, a gate or a reference that moves afterwards can be
applied to the same emissions with no second run.

Each sweep is its artifact, `<name>.json`, beside the directory
`<name>.reports/` the sweep wrote. An extraction or end-to-end sweep's
directory holds one `<case>.extraction.json` per case: what the node emitted
under `raw`, the model and gate verdict the sweep scored on the day, and under
`repair` what the repair node returned where one ran. An assertion sweep's holds
one `<case>.assertions.json`: the proposal, and the catalog the resolver built
on the day. The artifact records the commit, whether the tree was clean, the
corpus digest, the models and the instruction digest of the node, so the
replay places each sweep on an arm from the record rather than from its name.

Replay them with:

```sh
python -m evals.harness.run replay evals/emissions/*.json \
  evals/emissions/20260914T201030Z-assert-model-benchmark/*.json --out /tmp/replay.json
```

`tests/test_evals_replay.py` holds every artifact here to the loader, to one
arm, and to a replay that runs.

To add a sweep, copy its artifact and its `.reports/` directory from
`evals/runs/` unchanged. Do not rewrite a file: the bytes are what the sweep
wrote, and `evals/harness/archive.py` names the spelling each kind carries.

## What each sweep here is

| Sweep | Mode | What it measures |
| --- | --- | --- |
| `20260914T2200Z-extract-*`, `20260914T2205Z-extract-sweep` | extraction | the `extract.md` edit of #925, five runs each side |
| `20260914T201030Z-assert-model-benchmark` | assertions | three models on the `assert` node, #926 |
| `20260917T-arms-luna-pro` | heads | the head over 13 cases, one repeat, on luna-pro |
| `20260918T-placement-rule` | extraction | the placement rule of #1068, five runs each side |
| `20260919T-naming-rule` | extraction | the `extract.md` edits of #1076 and #1078, three runs |

The heads sweep reads through `extract` and `assert` and keeps the catalog its
own run gated, so `replay` grades it against the signed reference without
re-resolving against the blessed model.

The naming sweeps are one side. `r1..r3` ran on `d42e8f6` with rule 3's naming
exception from #1076 and the shorter asset vocabulary from #1078; **the other
side is `20260918T-placement-rule/after-r1..r5`**, which ran on `fcf08fb`
before either. They sit apart because they measure different edits on different
commits, and the arm a reader wants is the one the record names rather than the
one the directory does. The corpus moved between them, so replay both sides
rather than reading a recorded figure off either:

```sh
python -m evals.harness.run replay evals/emissions/20260919T-naming-rule/r*.json
python -m evals.harness.run replay evals/emissions/20260918T-placement-rule/after-r*.json
```

Read on 2026-09-19, `found` per sweep goes 114.8 to 128.3, which is 6.6 sd on
the before arm's own spread; `endpoint_unaligned` falls 8.6 to 1.3 and
`respelled` 4.0 to 0.3, and those two carry 11 of the 13.5. A flow ID derives
from its endpoints, so a flow the aligner could not match counted as lost.
`renamed` does not move, so the rule made names resolvable rather than more
numerous. Three runs rather than five: run 3 gated a Tier 1 failure on
07-cicd-store-deploy, where a model wrote a sentence into an assumption's
`attribute` field — the defect #1080 answers — and the operator's `set -e`
ended the loop.

The placement sweeps are the two sides of one `extract.md` edit on one commit
range. `before-r1..r5` ran on `fcf08fb`, where ADR 0039 had freed `trust_zone`
and the prompt said only not to choose a zone to fill the field; `after-r1..r5`
ran with the rule that names the three patterns a placement must not come from.
Score them with `docs/research/probe_placements.py`, which reads each case's
signed `network-membership` rows and charges every emitted component against
them.
