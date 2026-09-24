# Experiment record template

One JSON object per experiment. Write it to a file, then append it:

```bash
uv run python -m evals.harness.run experiments --record /tmp/<experiment-id>.json
```

`evals/harness/audit.py` checks every row before it is written. A missing
required field, an outcome outside the six, a phase outside the table, or an
`experiment_id` already on record is refused by name.

```json
{
  "experiment_id": "QA-2026-09-20-01-E3",
  "audit_id": "QA-2026-09-20-01",
  "recorded": "2026-09-20T14:02:11Z",
  "revision": "fd04ad1d369e51ec4dbfe8d51a305ff6b7015a80",
  "signature": "stride case-09: the authentication lane cites the place and writes another action",
  "hypothesis": "the shipped exemplar teaches a verb the reference set does not accept",
  "falsifier": "price-verbs shows the equivalence merges labelled non-matches, or the verb rows in that lane number fewer than the band",
  "intervention": "priced the equivalence on the frontier against the d3f1898 Baseline",
  "outcome": "null",
  "phase": "analysis",
  "framework": "stride",
  "case": "09-cookbook-sokify-retail",
  "scope": "case 09 only, stride, one archived Baseline, scorer at this revision",
  "reads": [
    "evals/harness/verbs.py",
    "frameworks/stride/lanes/authentication/skill.md",
    "evals/corpus/09-*/reference.json"
  ],
  "artifacts": [
    "/tmp/09-rescored.json"
  ],
  "estimated_usd": null,
  "actual_usd": null,
  "parent": null,
  "supersedes": null,
  "reconsider_when": "the case-09 reference set is re-ruled, or the identity rule versions"
}
```

## The fields that are easy to get wrong

**`signature`** is the lookup key. Write the *observed failure*, not the fix:
the next audit will see the same failure and phrase the fix differently. Keep
it stable across audits.

**`falsifier`** is what would have made you wrong. A hypothesis with no
falsifier is not an experiment, and the loader refuses the row.

**`scope`** is what the conclusion covers — which cases, which framework, which
models, which scorer. A conclusion stated wider than its scope is the failure
this field exists to stop.

**`reads`** is what makes staleness decidable. List the repository paths the
conclusion actually depends on. Leave it out and the row reads `unanchored`
forever.

**`framework`** is checked against `PACKAGES`, and `null` means the conclusion
is **neutral** — it holds whatever package ran. That is a different claim from
holding for one package, and the lookup treats it as such: a neutral row
answers for every framework, and a `stride` row does not answer for `asvs`. Say
which you mean.

**`outcome`** is one of `supported`, `refuted`, `null`, `inconclusive`,
`blocked`, `superseded`. An experiment stopped by an exhausted budget is
`inconclusive`, not `null`. An experiment that could not run at all is
`blocked`.

**`estimated_usd` and `actual_usd`** are `null` for an offline experiment. That
is not zero: an offline experiment has no provider cost to state.

**`condition`** is `null`, except for an experiment in
`references/three-conditions.md`. There it is an object, and the loader
refuses a missing or unknown field by name:

```json
"condition": {
  "name": "corrected-extraction",
  "input": "evals/corpus/*/model.json at 817e86d",
  "adapters": [],
  "unrepresentable": ["case 12: the supplier's placement"],
  "exposure": "signed by the maintainer before this audit read any miss",
  "stage": "final-report"
}
```

`name` is one of `current-pipeline`, `corrected-extraction` or `direct-facts`.
`stage` is `analysis` or `final-report`, and the loader refuses a stage the
condition cannot reach, as `CONDITION_STAGES` in `evals/harness/audit.py`
states. A `lane-replay` reading is `analysis`; a `run --mode direct-facts`
reading is `final-report`.
`adapters` and `unrepresentable` may be empty lists, but they must be present,
because an empty list says "none" and a missing field says nothing.

**`supersedes`** names the row this one corrects. The corrected row stays in
the ledger — it is the record of what was wrong.
