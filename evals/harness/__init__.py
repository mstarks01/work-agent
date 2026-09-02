"""The golden-case eval harness.

Nothing here ships in the production image, because the wheel builds only
``src/analysis_service``.

The modules, in the order a run uses them:

* :mod:`evals.harness.reference` — ``ReferenceThreat`` and the corpus loader.
* :mod:`evals.harness.structural` — the Tier 1 gates, the only ones that block.
* :mod:`evals.harness.identity` — the rule that decides when two claims are one
  finding, and the ``Matcher`` protocol it answers through.
* :mod:`evals.harness.ledger` — the append-only record of what a person decided
  about a finding, which is where the standing of an unmatched one comes from.
* :mod:`evals.harness.scorer` — matching, standing and the metrics.
* :mod:`evals.harness.calibration` — rule-vs-label agreement over the fixtures.
* :mod:`evals.harness.modes` — the three eval modes over one corpus.
* :mod:`evals.harness.run` — the CLI that ties them together.
"""
