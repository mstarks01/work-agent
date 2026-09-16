# Archived emissions

What `extract` and `assert` said in sweeps this repository paid for, kept so
that a scorer, a normalizer, a gate or a reference that moves afterwards can be
applied to the same emissions with no second run.

Each sweep is its artifact, `<name>.json`, beside the directory
`<name>.reports/` the sweep wrote. An extraction sweep's directory holds one
`<case>.extraction.json` per case: what the node emitted under `raw`, and the
model and gate verdict the sweep scored on the day. An assertion sweep's holds
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
