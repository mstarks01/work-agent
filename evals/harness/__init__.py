"""The golden-case eval harness (wayfinder ticket 023).

Nothing here ships in the production image: the wheel builds only
``src/stride_service``, and the judge prompt deliberately lives under
``evals/prompts/`` rather than the lint-governed ``prompts/`` tree (ticket 009
decision 14).

Modules, in the order a run uses them:

* :mod:`evals.harness.reference` — ``ReferenceThreat`` and the corpus loader.
* :mod:`evals.harness.structural` — the Tier 1 gates, the only ones that block.
* :mod:`evals.harness.judge` — the pinned claim-equivalence judge and its seam.
* :mod:`evals.harness.scorer` — mechanical-first matching and the metrics.
* :mod:`evals.harness.calibration` — judge-vs-human agreement over the fixtures.
* :mod:`evals.harness.modes` — the three eval modes over one corpus.
* :mod:`evals.harness.run` — the CLI that ties them together.
"""
