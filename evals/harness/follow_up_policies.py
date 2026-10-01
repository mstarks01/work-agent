"""Compare orders of a report's follow-up questions at a matched number of choices.

A report asks the open facts its conditional findings wait on (ADR 0054), in
the order :func:`~analysis_service.questions.follow_up_order` sets (ADR 0055).
The #1289 review of 2026-10-01 asked what "most important first" should mean:
the most findings completed at each step, or the most important finding's
facts first, even where they are two. This command answers that offline, for
an owner who answers from the top and stops after a number of choices.

**What an order is worth is read off archived reports.** A conditional finding
is complete when every open fact it waits on is answered
(:func:`~analysis_service.questions.follow_up_needs` reads them). The count is
by band, as each package ranks its findings. It is a count of dependency sets
an order completes, never a count of findings an answer changes.

**Effort is choices**: one a facet for a question answered in facets, and one
for every other question.

**Each order is a policy in** :data:`POLICIES`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from analysis_service.claims import UnknownKey
from analysis_service.critic import complete_rulings
from analysis_service.frameworks import SCHEMAS
from analysis_service.questions import (
    Basis,
    Finding,
    FollowUpNeeds,
    choices_of,
    follow_up_needs,
    follow_up_order,
)
from analysis_service.report import Report

#: The owner effort, in choices, at which each order is read.
BUDGETS = (1, 2, 3, 5, 10, 15, 20)


def _completion_first(needs: FollowUpNeeds) -> list[tuple[Basis, UnknownKey]]:
    """The order ADR 0055 shipped: each draft's grounds first, then the facts
    only the critic named, each section in the order that completes the most
    findings, highest band first."""
    band = {finding: each.order for finding, each in needs.bands.items()}
    first = _completing(needs.evidence, band)
    asked = set(first)
    later = _completing({f: keys - asked for f, keys in needs.named.items()}, band)
    return [*(("evidence", key) for key in first), *(("critic", key) for key in later)]


def _completing(
    open_facts: Mapping[Finding, AbstractSet[UnknownKey]], band: Mapping[Finding, int]
) -> list[UnknownKey]:
    """The facts in the order that completes the most important findings first.

    ``band`` is each finding's :class:`~analysis_service.frameworks.Band`
    order. Where one question completes findings, the next is the one that
    completes the most in the highest band, then in the next band down, and so
    on: one critical finding before three low ones, with no weights. Where
    none does, the next are the facts of the highest-band finding with the
    fewest left, the most cited first. A tie goes to more citations, then to
    the lower key, so one input always gives one order. Measured against
    asking the most cited fact first, completing the most findings covers more
    at every depth (``QA-2026-09-26-03-E17``).
    """
    left = {finding: set(keys) for finding, keys in open_facts.items() if keys}
    order: list[UnknownKey] = []
    while left:
        cites = Counter(key for keys in left.values() for key in keys)
        bands = sorted({band[finding] for finding in left}, reverse=True)
        completes: dict[UnknownKey, Counter[int]] = {}
        for finding, keys in left.items():
            if len(keys) == 1:
                completes.setdefault(next(iter(keys)), Counter())[band[finding]] += 1
        if completes:
            chosen = [
                min(
                    completes,
                    key=lambda key: (
                        tuple(-completes[key][level] for level in bands),
                        -cites[key],
                        key,
                    ),
                )
            ]
        else:
            nearest = min(
                left.items(),
                key=lambda item: (-band[item[0]], len(item[1]), sorted(item[1])),
            )[1]
            chosen = sorted(nearest, key=lambda key: (-cites[key], key))
        order.extend(chosen)
        asked = set(chosen)
        left = {finding: keys - asked for finding, keys in left.items() if keys - asked}
    return order


#: Every order compared, by name.
POLICIES: Mapping[str, Callable[[FollowUpNeeds], list[tuple[Basis, UnknownKey]]]] = {
    "shipped": follow_up_order,
    "completion-first": _completion_first,
}


@dataclass(frozen=True)
class Reading:
    """One order's worth to one report at one budget."""

    report: str
    framework: str
    policy: str
    #: The budget in choices, or ``None`` for the whole order.
    budget: int | None
    choices: int
    findings: int
    completed: int
    #: Findings and completed findings by band label.
    finding_bands: Mapping[str, int]
    completed_bands: Mapping[str, int]


def readings(path: str, report: Report, built: Report | None = None) -> list[Reading]:
    """Every policy's reading on one report, at every budget.

    ``built`` is the report each order is built from, where it is not the one
    it is scored on: the same drafts under another critic sample.
    """
    (framework,) = (selection.name for selection in report.job.frameworks)
    needs = follow_up_needs(report.analyses, report.system_model, None)
    source = (
        needs
        if built is None
        else follow_up_needs(built.analyses, built.system_model, None)
    )
    labels = {finding: needs.bands[finding].label for finding in needs.waiting}
    found = []
    for policy, build in POLICIES.items():
        order = [key for _, key in build(source)]
        for budget in (*BUDGETS, None):
            spent = 0
            answered: set[UnknownKey] = set()
            for key in order:
                if budget is not None and spent + choices_of(key) > budget:
                    break
                spent += choices_of(key)
                answered.add(key)
            done = [
                labels[finding]
                for finding, keys in needs.waiting.items()
                if keys <= answered
            ]
            found.append(
                Reading(
                    report=path,
                    framework=framework,
                    policy=policy,
                    budget=budget,
                    choices=spent,
                    findings=len(needs.waiting),
                    completed=len(done),
                    finding_bands=dict(Counter(labels.values())),
                    completed_bands=dict(Counter(done)),
                )
            )
    return found


def with_rulings(report: Report, rulings: Path) -> Report:
    """The report with one critic sample's verdicts on its drafts.

    Each ruling is completed against its draft as the service completes it
    (:func:`~analysis_service.critic.complete_rulings`), so a draft that rests
    on an unstated fact reads ``needs-info`` here as it would in a report.
    """
    blocks = []
    for block in report.analyses:
        batch = SCHEMAS[block.framework].rulings.model_validate_json(
            rulings.read_text(encoding="utf-8")
        )
        drafts = block.all_claims()
        verdicts = {
            ruling.id: ruling.verdict
            for ruling in complete_rulings(list(drafts), batch.claims)
        }
        ruled = [
            claim.model_copy(update={"verdict": verdicts.get(claim.id, claim.verdict)})
            for claim in drafts
        ]
        blocks.append(block.model_copy(update={"claims": ruled, "rejected_claims": []}))
    return report.model_copy(update={"analyses": blocks})


def crossed(baseline: Path, samples: Sequence[Path]) -> list[Reading]:
    """Each order built from one critic sample and scored on the other.

    ``baseline`` holds the reports whose drafts both samples ruled on, and
    each sample directory a ``<case>.rulings.json`` per case. An order that
    reads only the drafts is the same for both samples; one that reads the
    verdicts is built from one sample and scored on the other, as a report
    read again would be.
    """
    found = []
    for path in sorted(baseline.rglob("*.report.json")):
        case = path.name.removesuffix(".report.json")
        sources = [sample / f"{case}.rulings.json" for sample in samples]
        if not all(source.is_file() for source in sources):
            continue
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        for build_from, score_on in (
            (sources[0], sources[1]),
            (sources[1], sources[0]),
        ):
            built = with_rulings(report, build_from)
            scored = with_rulings(report, score_on)
            found.extend(
                readings(f"{build_from} on {score_on.parent.name}", scored, built)
            )
    return found


def sweep(root: Path) -> list[Reading]:
    """Every archived report of one framework under ``root``."""
    found = []
    for path in sorted(root.rglob("*.report.json")):
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        if len(report.job.frameworks) == 1:
            found.extend(readings(str(path), report))
    return found


#: The counts a summary sums over reports, each read off a reading.
SUMMED = ("choices", "findings", "completed")


def summary(found: Sequence[Reading]) -> dict[str, dict[str, dict[str, int]]]:
    """Sums over reports, by framework, policy and budget."""
    sums: dict[str, dict[str, Counter[str]]] = {}
    for one in found:
        group = sums.setdefault(one.framework, {})
        row = group.setdefault(
            f"{one.policy} @ {'all' if one.budget is None else one.budget}", Counter()
        )
        row["reports"] += 1
        for field in SUMMED:
            row[field] += getattr(one, field)
        for band, count in one.completed_bands.items():
            row[f"completed {band}"] += count
        for band, count in one.finding_bands.items():
            row[f"findings {band}"] += count
    return {
        group: {key: dict(sorted(row.items())) for key, row in rows.items()}
        for group, rows in sums.items()
    }


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("evals"),
        help="the directory whose archived *.report.json files are read",
    )
    parser.add_argument("--out", type=Path, help="write the summary here as JSON")
    parser.add_argument(
        "--samples",
        type=Path,
        nargs=2,
        help="two critic replays' rulings directories: build each order from"
        " one and score it on the other, over the reports under --root",
    )


def command_follow_up_policies(args: argparse.Namespace) -> int:
    """Read every policy on every archived report, and print the sums."""
    found = (
        sweep(args.root) if args.samples is None else crossed(args.root, args.samples)
    )
    if not found:
        print("no archived report under the root", file=sys.stderr)
        return 1
    table = summary(found)
    print(json.dumps(table, indent=2))
    if args.out is not None:
        args.out.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    return 0
