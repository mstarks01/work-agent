"""Does a level 1 ASVS job rank its early questions better by level 1 findings only?

#1289, item B3: the early-question prior counts the citations of every ASVS
level, so a level 1 job is ranked partly by level 2 findings it never makes.
Two arms of the ASVS prior, scored on the archived level 1 ASVS reports:

* ``pooled``: every finding's citations, as the shipped prior counts them;
* ``banded``: only the citations of level 1 findings.

Each arm is read two ways for the prior: ``in-sample``, counted from the
whole prior run, and ``held-out``, counted from the prior run without the
scored case. Each is read two ways for the order: ``rounds``, the shipped
rounds with the floor, and ``ranked``, the early list without the floor. The
ranked lists of both arms hold the same questions, so they compare at equal
choices. A case's figure is the mean over its reports, so each case weighs
one.

Run from the repository root:
``ANALYSIS_OFFLINE=1 uv run python evals/experiments/QA-2026-09-26-03/early-prior-bands.py``
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from analysis_service import early_questions as eq
from analysis_service.early_questions import PriorRow, early_questions
from analysis_service.frameworks import PACKAGES
from analysis_service.report import Report
from evals.harness.bundle import reports_dir
from evals.harness.early_policies import _prefix, _shipped_rounds, needs_of
from evals.harness.question_prior import count

PRIOR_RUN = Path("evals/runs/20260906T234806Z-asvs-two-question-t1/analysis-asvs.json")
BUDGETS = (5, 10, 15, 20, 30, None)
OUT = Path(__file__).with_name("early-prior-bands.json")
#: The band labels each arm counts; ``None`` counts every band.
ARMS = {"pooled": None, "banded": {"level 1"}}
SELECTION = {"asvs": {"level": 1}}


def prior_samples():
    """Each case of the prior run: its model and each finding's band and facts."""
    rank = PACKAGES["asvs"].rank
    samples = []
    for path in sorted(reports_dir(PRIOR_RUN).glob("*.report.json")):
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        findings = [
            (rank(claim).label, list(claim.verdict.related_unknowns))
            for block in report.analyses
            if block.framework == "asvs"
            for claim in block.all_claims()
            if claim.verdict.related_unknowns
        ]
        samples.append(
            (path.name.removesuffix(".report.json"), report.system_model, findings)
        )
    return samples


def prior_of(samples, held_out, bands):
    """The shipped table with its ASVS row counted from these samples and bands."""
    kept = [
        (case, model, [refs for band, refs in found if bands is None or band in bands])
        for case, model, found in samples
        if case != held_out
    ]
    row = PriorRow(runs=("probe",), revision=None, cases=len(kept), rates=count(kept))
    return {**eq.QUESTION_PRIOR, "asvs": row}


def order(model, prior, how):
    """The order an owner meets: the shipped rounds, or the ranked list."""
    eq.early_questions.__defaults__ = (prior,)
    if how == "rounds":
        return _shipped_rounds(model, SELECTION)
    return list(early_questions(model, SELECTION, None, prior))


def level_1_reports():
    """Every archived report of a job that selected ASVS at level 1 alone."""
    for path in sorted(Path("evals").rglob("*.report.json")):
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        chosen = {s.name: dict(s.options) for s in report.job.frameworks}
        if chosen == SELECTION:
            yield path.name.removesuffix(".report.json"), str(path), report


def main() -> int:
    samples = prior_samples()
    rows = []
    try:
        for case, path, report in level_1_reports():
            needs = needs_of(report)
            for source in ("in-sample", "held-out"):
                for arm, bands in ARMS.items():
                    prior = prior_of(
                        samples, case if source == "held-out" else None, bands
                    )
                    for how in ("rounds", "ranked"):
                        listed = order(report.system_model, prior, how)
                        for budget in BUDGETS:
                            answered = _prefix(listed, budget)
                            rows.append(
                                {
                                    "case": case,
                                    "report": path,
                                    "prior": source,
                                    "arm": arm,
                                    "order": how,
                                    "budget": budget,
                                    "choices": sum(
                                        q.decisions for q in listed if q.key in answered
                                    ),
                                    "findings": len(needs.findings),
                                    "completed": sum(
                                        1
                                        for _, keys in needs.findings.values()
                                        if keys <= answered
                                    ),
                                }
                            )
    finally:
        eq.early_questions.__defaults__ = (eq.QUESTION_PRIOR,)
    summary = summarise(rows)
    OUT.write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2) + "\n",
        encoding="utf-8",
    )
    json.dump(summary, sys.stdout, indent=2)
    return 0


def summarise(rows):
    """Per-case means summed over cases, and the cases where ``banded`` wins."""
    per = defaultdict(list)
    for row in rows:
        per[
            (row["prior"], row["order"], row["budget"], row["arm"], row["case"])
        ].append(row)
    out = {}
    for prior in ("in-sample", "held-out"):
        for how in ("rounds", "ranked"):
            for budget in BUDGETS:
                cases = sorted(
                    {key[4] for key in per if key[:3] == (prior, how, budget)}
                )
                line = {"cases": len(cases), "by_case": {}}
                sums = {arm: {"completed": 0.0, "choices": 0.0} for arm in ARMS}
                verdicts = {"better": 0, "worse": 0, "same": 0}
                for case in cases:
                    means = {}
                    for arm in ARMS:
                        got = per[(prior, how, budget, arm, case)]
                        done = sum(r["completed"] for r in got) / len(got)
                        sums[arm]["completed"] += done
                        sums[arm]["choices"] += sum(r["choices"] for r in got) / len(
                            got
                        )
                        means[arm] = round(done, 2)
                    line["by_case"][case] = means
                    if means["banded"] > means["pooled"]:
                        verdicts["better"] += 1
                    elif means["banded"] < means["pooled"]:
                        verdicts["worse"] += 1
                    else:
                        verdicts["same"] += 1
                for arm in ARMS:
                    line[arm] = {k: round(v, 2) for k, v in sums[arm].items()}
                line["banded_cases"] = verdicts
                out[f"{prior} / {how} @ {'all' if budget is None else budget}"] = line
    return out


if __name__ == "__main__":
    raise SystemExit(main())
