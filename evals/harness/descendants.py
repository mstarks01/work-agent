"""Replay a sweep's fan-in over its archived proposals, with proposals injected (#1091).

A finding can be lost after the lane that wrote it: the fan-in drops or
narrows it, the critic rejects it, or nothing wrote it at all. This answers the
question "would this finding have survived, if a lane had proposed it?" for no
provider call, by running the stages after the lane again on the archive.

**What runs, and what is carried.** The fan-in runs as it ships, over each
case's archived ``<case>.proposals.json`` and any injected proposal added to
its lane. The critic does not run. A draft it already ruled on keeps that
ruling, matched by title, because the fan-in keeps a proposal's title and the
critic judged the argument, not the element list. A draft it never saw — every
injected one, and any a code change creates — has no ruling, so the result is
two bounds: ``lower`` counts it rejected, ``upper`` counts it accepted. A
reference inside ``upper`` and outside ``lower`` is one a critic call decides,
and ``lane-replay``'s ``node_call`` is the seam that makes that one call.

**What it does not re-run.** Assembly. A kept draft is read as the critic's
ruled claim, which is what assembly makes of it: ``run.py assembly`` rebuilds
the report of every archived STRIDE and ASVS block today's review check
accepts, and found no claim moved and no ruled draft omitted.

Scored by the STRIDE scorer, whose identity composes an action and a place, so
it reads the proposals of a package that proposes an open claim set.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analysis_service.claims import FrameworkName
from analysis_service.fan_in import fan_in
from analysis_service.frameworks import PACKAGES, schemas_for
from analysis_service.frameworks.stride.record import DraftThreat
from analysis_service.report import Report
from analysis_service.system_model import ModelIndex
from evals.harness.bundle import reports_dir, stride_threats
from evals.harness.identity import SubsetVerbIdentity
from evals.harness.ledger import Ledger
from evals.harness.modes import EvalRunError
from evals.harness.reference import GoldenCase, load_corpus, tuning_cases
from evals.harness.scorer import score_case

FRAMEWORK: FrameworkName = "stride"


@dataclass(frozen=True)
class Replayed:
    """One case's references, as the archive matched them and as the replay does."""

    case: str
    archived: frozenset[int]
    lower: frozenset[int]
    upper: frozenset[int]
    must_find: frozenset[int]
    #: Titles of the drafts no critic ruled on.
    unruled: tuple[str, ...]
    #: Each injected proposal the fan-in dropped, with the reason it recorded.
    dropped: tuple[tuple[str, str], ...]


def _matched(case: GoldenCase, produced: Sequence[DraftThreat]) -> frozenset[int]:
    flows = ModelIndex.of(case.model).flow_endpoints
    score = score_case(case, produced, SubsetVerbIdentity({case.id: flows}), Ledger())
    return frozenset(pair.reference_index for pair in score.matched)


def replay_case(
    case: GoldenCase,
    report: Report,
    proposals: Mapping[str, Any],
    injected: Mapping[str, Sequence[Mapping[str, Any]]] = {},
) -> Replayed:
    """The fan-in run again over ``proposals`` plus ``injected``, then scored."""
    block = next(block for block in report.analyses if block.framework == FRAMEWORK)
    schema = schemas_for(FRAMEWORK).proposals
    batches = {}
    for lane in set(proposals) | set(injected):
        raw = dict(proposals.get(lane) or {"claims": []})
        raw["claims"] = [*raw.get("claims", []), *injected.get(lane, ())]
        batches[lane] = schema.model_validate(raw)
    merged = fan_in(
        batches,
        PACKAGES[FRAMEWORK],
        report.system_model,
        {source.label: source.text for source in case.sources},
    )
    accepted = {claim.title: claim for claim in stride_threats(report)}
    ruled = accepted.keys() | {claim.title for claim in block.rejected_claims}
    kept = [
        accepted[draft.title].model_copy(
            update={"affected_element_ids": draft.affected_element_ids}
        )
        for draft in merged.drafts
        if draft.title in accepted
    ]
    unruled = [
        draft
        for draft in merged.drafts
        if draft.title not in ruled and isinstance(draft, DraftThreat)
    ]
    titles = {
        str(claim.get("title", "")) for claims in injected.values() for claim in claims
    }
    dropped = tuple(
        (mark.title, mark.reason)
        for mark in merged.marks.dropped_claims
        if mark.title in titles
    )
    references = case.stride_claims()
    return Replayed(
        case=case.id,
        archived=_matched(case, stride_threats(report)),
        lower=_matched(case, kept),
        upper=_matched(case, [*kept, *unruled]),
        must_find=frozenset(
            index for index, reference in enumerate(references) if reference.must_find
        ),
        unruled=tuple(draft.title for draft in unruled),
        dropped=dropped,
    )


def replay_artifact(
    artifact: Path,
    cases: Sequence[GoldenCase],
    injected: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]] = {},
) -> list[Replayed]:
    """Every case of a sweep that archived its proposals, replayed."""
    directory = reports_dir(artifact)
    rows = []
    for case in cases:
        report_path = directory / f"{case.id}.report.json"
        proposals_path = directory / f"{case.id}.proposals.json"
        if not report_path.is_file():
            continue
        if not proposals_path.is_file():
            raise EvalRunError(
                f"{proposals_path} is missing; the sweep kept no lane proposals,"
                " so its fan-in cannot be run again"
            )
        report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
        proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
        rows.append(
            replay_case(
                case, report, proposals.get(FRAMEWORK, {}), injected.get(case.id, {})
            )
        )
    return rows


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("artifact", type=Path, help="a sweep with archived proposals")
    parser.add_argument("--case", action="append", default=[], help="limit to a case")
    parser.add_argument(
        "--inject",
        type=Path,
        help="JSON: case ID, then lane, then a list of proposals to add to it",
    )
    parser.add_argument("--corpus", type=Path, default=Path("evals/corpus"))


def command_descendants(args: argparse.Namespace) -> int:
    """Replay a sweep's fan-in, with anything injected, and print both bounds."""
    injected = (
        json.loads(args.inject.read_text(encoding="utf-8")) if args.inject else {}
    )
    cases = [
        case
        for case in tuning_cases(load_corpus(args.corpus))
        if not args.case or case.id in args.case
    ]
    try:
        rows = replay_artifact(args.artifact, cases, injected)
    except EvalRunError as error:
        print(error, file=sys.stderr)
        return 1
    print("case                               must-finds: archived  lower  upper")
    for row in rows:
        counts = [
            len(matched & row.must_find)
            for matched in (row.archived, row.lower, row.upper)
        ]
        print(f"{row.case:34} {counts[0]:>19} {counts[1]:>6} {counts[2]:>6}")
        for index in sorted(row.upper - row.archived):
            flag = "must-find" if index in row.must_find else "reference"
            decided = "kept" if index in row.lower else "needs a critic ruling"
            print(f"  + {flag} {index}: {decided}")
        for index in sorted(row.archived - row.lower):
            print(f"  - reference {index}: matched in the archive, not in the replay")
        for title, reason in row.dropped:
            print(f"  injected and dropped: {title!r}: {reason}")
    return 0
