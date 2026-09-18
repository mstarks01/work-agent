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
| `20260917T-arms-luna-pro` | heads | #1003's arms A, B and E over 13 cases, one repeat each |
| `20260918T-placement-rule` | extraction | the placement rule of #1068, five runs each side |

The arms sweep carries three reading routes: `arm-A` reads through `extract`,
`arm-B2` through `facts`, and `arm-E` through the split `inventory` and `rows`
calls. `evals/harness/replay.py`'s `ROUTES_OF` places each on its own arm, and
`run.py bottleneck misses` charges every missed reference row of the three to
the earliest stage that did not carry it.

The placement sweeps are the two sides of one `extract.md` edit on one commit
range. `before-r1..r5` ran on `fcf08fb`, where ADR 0039 had freed `trust_zone`
and the prompt said only not to choose a zone to fill the field; `after-r1..r5`
ran with the rule that names the three patterns a placement must not come from.
Score them with `docs/research/probe_placements.py`, which reads each case's
signed `network-membership` rows and charges every emitted component against
them.
