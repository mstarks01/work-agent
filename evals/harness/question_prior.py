"""Count the early-question prior from archived runs (``QA-2026-09-26-03-E13``).

The prior says how often a framework's findings cite each attribute or
question kind, per element of one type. :mod:`analysis_service.early_questions`
ranks the questions a paused job asks with it. This command counts it from a
sweep's reports, or from critic replays of that sweep's drafts, and writes the
one framework's row of ``question_prior.json`` with the runs it read.

**Only tuned cases are counted.** A holdout case's findings never shape a
table that the service ranks with.

**A rate is citations per element.** Each finding's open facts are counted
once per reference, and each sample counts its model's elements again, so two
samples of one sweep average rather than double.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from analysis_service.claims import FrameworkName, UnknownRef
from analysis_service.early_questions import (
    QUESTION_PRIOR_PATH,
    PriorRow,
    element_type,
    load_prior,
)
from analysis_service.frameworks import PACKAGES
from analysis_service.report import Report
from analysis_service.system_model import SystemModel
from evals.harness.artifact import repo_commit
from evals.harness.bundle import reports_dir
from evals.harness.reference import (
    CorpusError,
    corpus_argument,
    load_corpus,
    tuning_cases,
)

#: One case's sample: the case, its model and each finding's open facts.
Sample = tuple[str, SystemModel, list[list[UnknownRef]]]


def report_findings(report: Report, framework: FrameworkName) -> list[list[UnknownRef]]:
    """Each finding's open facts, as the report's verdicts name them."""
    return [
        list(claim.verdict.related_unknowns)
        for block in report.analyses
        if block.framework == framework
        for claim in block.all_claims()
        if claim.verdict.related_unknowns
    ]


def replay_findings(path: Path) -> list[list[UnknownRef]]:
    """Each ruling's open facts, as a critic replay wrote them."""
    claims = json.loads(path.read_text(encoding="utf-8"))["claims"]
    return [
        [UnknownRef.model_validate(ref) for ref in refs]
        for claim in claims
        if (refs := claim["verdict"].get("related_unknowns"))
    ]


def count(samples: Iterable[Sample]) -> dict[str, dict[str, float]]:
    """Citations of each attribute or kind, per element of each type."""
    cited: Counter[tuple[str, str]] = Counter()
    elements: Counter[str] = Counter()
    for _, model, findings in samples:
        types = {element.id: element_type(element) for element in model.elements()}
        elements.update(types.values())
        for refs in findings:
            for ref in refs:
                named = ref.attribute or ref.question
                if len(ref.spellings) == 1 and named and ref.element_id in types:
                    cited[(types[ref.element_id], named)] += 1
    rates: dict[str, dict[str, float]] = {}
    for (kind, named), times in sorted(cited.items()):
        rates.setdefault(kind, {})[named] = round(times / elements[kind], 4)
    return rates


def samples_of(
    artifact: Path,
    replays: Sequence[Path],
    framework: FrameworkName,
    cases: Sequence[str],
) -> list[Sample]:
    """Every sample the named runs hold for these cases."""
    directory = reports_dir(artifact)
    out: list[Sample] = []
    for case in cases:
        path = directory / f"{case}.report.json"
        if not path.is_file():
            continue
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        if not replays:
            out.append((case, report.system_model, report_findings(report, framework)))
        for replay in replays:
            ruled = replay / f"{case}.rulings.json"
            if ruled.is_file():
                out.append((case, report.system_model, replay_findings(ruled)))
    return out


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "artifact", type=Path, help="the sweep whose reports hold the models"
    )
    parser.add_argument(
        "--framework", required=True, choices=sorted(PACKAGES), help="the package"
    )
    parser.add_argument(
        "--replay",
        type=Path,
        action="append",
        default=[],
        help="a critic-replay --out directory of this sweep's drafts; read its"
        " rulings in place of the reports' verdicts. Repeat for each sample",
    )
    parser.add_argument(
        "--out", type=Path, default=QUESTION_PRIOR_PATH, help="the prior table"
    )
    corpus_argument(parser)


def command_question_prior(args: argparse.Namespace) -> int:
    """Count one framework's prior and write its row, keeping the other rows."""
    try:
        cases = [case.id for case in tuning_cases(load_corpus(args.corpus))]
    except CorpusError as error:
        print(error, file=sys.stderr)
        return 1
    samples = samples_of(args.artifact, args.replay, args.framework, cases)
    if not samples:
        print("no tuned case has a report under that artifact", file=sys.stderr)
        return 1
    table = dict(load_prior(args.out)) if args.out.is_file() else {}
    for name in PACKAGES:
        table.setdefault(name, PriorRow(runs=(), revision=None, cases=0, rates={}))
    counted = {case for case, _, _ in samples}
    table[args.framework] = PriorRow(
        runs=(str(args.artifact), *map(str, args.replay)),
        revision=repo_commit().commit,
        cases=len(counted),
        rates=count(samples),
    )
    args.out.write_text(
        json.dumps({name: table[name].to_json() for name in sorted(table)}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    rows = sum(len(fields) for fields in table[args.framework].rates.values())
    print(
        f"{args.framework}: {rows} rates from {len(counted)} tuned cases and"
        f" {len(samples)} samples, written to {args.out}"
    )
    return 0
