"""Which findings a reviewer is asked about, and in what order.

Human attention is the scarce resource in this whole design, so the order is a
budget rather than a convenience. A finding twenty runs already agree on buys
nothing. A finding that appears in one run of five, or that two configurations
disagree about, buys the most information a single click can.

It is built from finished artifacts and the ledger, and nothing else. There is
no provider, no credential and no engine. That is what lets a sitting happen on
a laptop with the repository checked out, and it is what makes the queue
reproducible: the same artifacts and the same ledger produce the same queue in
the same order.

It is blind by construction. :class:`QueueItem` carries no model name, no tier
and no configuration label. Those live on the
:class:`~evals.harness.ledger.Vote`, and are stamped after the answer. A
reviewer who can see which configuration produced a finding is a reviewer whose
vote can encode a preference about the configuration, which is exactly the bias
the vote exists to escape.

It skips what is already answered. A vote is spent once and kept for ever,
because it hangs on a fingerprint, so the second sitting over a corpus sees only
what the first did not: new findings from a changed configuration, and
disagreements. That is the whole economic argument for the fingerprint.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from analysis_service.report import FrameworkName
from evals.harness import ledger
from evals.harness.fingerprint import Components, key_claim
from evals.harness.identity import FlowMap
from evals.harness.ledger import Ledger


@dataclass(frozen=True)
class Finding:
    """One produced claim, reduced to what a reviewer and the queue read.

    Deliberately not a :class:`~analysis_service.report.Claim`: a queue item must
    not carry a field a reviewer should not see, and the cheapest way to
    guarantee that is a type that has no room for one.
    """

    case: str
    framework: FrameworkName
    lane: str
    title: str
    description: str
    element_ids: tuple[str, ...]
    quotes: tuple[str, ...] = ()
    verb: str | None = None
    #: The catalog requirement this claim names, for a package whose claims
    #: carry one. ``None`` where the package composes its identity instead.
    identifier: str | None = None
    #: How many runs of the sweep produced this finding, out of how many ran.
    seen_in: int = 1
    runs: int = 1


@dataclass(frozen=True)
class QueueItem:
    """One question for a reviewer, with everything needed to answer it.

    ``why`` is shown to the reviewer as the reason they are being asked. Telling
    somebody *why* their answer matters is the difference between a queue and a
    chore, and it costs one string.
    """

    fingerprint: str
    components: Components
    finding: Finding
    priority: int
    why: str

    @property
    def volatile(self) -> bool:
        """Did the sweep disagree with itself about this finding?"""
        return 0 < self.finding.seen_in < self.finding.runs

    def to_json(self) -> dict[str, Any]:
        """What the review page reads, and nothing else.

        Every key here is one the page renders. The fields it does not render
        stay on the dataclass, where the server reads them: the vote route takes
        `components` from the session rather than from the browser, and the
        question is picked from `finding.framework` here rather than sent. A key
        the page ignores is a field a reviewer's browser holds for no reason,
        and this payload is the one the queue's blindness rests on.
        """
        return {
            "fingerprint": self.fingerprint,
            "case": self.finding.case,
            "lane": self.finding.lane,
            "title": self.finding.title,
            "description": self.finding.description,
            "element_ids": list(self.finding.element_ids),
            "quotes": list(self.finding.quotes),
            "why": self.why,
        }


#: What a reason is worth, highest first. A table rather than a chain of ``if``
#: so that adding a reason is a row and the order is readable in one place —
#: the same rule ``evals/harness/instruments.py`` applies to measurements.
#:
#: The ordering is an argument about information, not about importance: a
#: finding the sweep disagreed with itself about splits a recall number two
#: ways, so one answer settles more than an answer about a finding every run
#: produced.
PRIORITIES: tuple[tuple[str, int, str], ...] = (
    (
        "volatile",
        30,
        (
            "the sweep found this in some runs and not others, so one answer here"
            " settles which way a recall number should have gone"
        ),
    ),
    (
        "new",
        10,
        (
            "nobody has answered this finding before, so it counts in no number"
            " until somebody does"
        ),
    ),
)

# There was a third row between these two, ``unmatched``, weighing whether the
# reference pool already carried the finding. It is gone because it could not
# answer that question without answering a different one.
#
# The pool is derived from votes, and this queue skips what is already
# answered. In an unnamed queue "answered" is every fingerprint anybody voted
# on, and the pool is a subset of that, so nothing pooled ever survived to be
# ranked and the row fired on everything. In a queue built for a named voter
# "answered" is only that voter's, so a pooled finding that survives was pooled
# by *somebody else* -- and the row's weight, its position in the order and the
# reason printed beside it then told this reviewer how another reviewer had
# voted. The second-opinion pass is the one place that must not be told.
#
# So the row was dead where it was safe and an oracle where it was live. Ranking
# by the corpus reference sets would be a different row, honestly answering the
# question this one's prose claimed; it is not this one.


def priority_of(finding: Finding) -> tuple[int, str]:
    """The first reason that applies, with its weight — never the sum.

    Summing would rank a finding that is merely new above a volatile one, and
    volatility is the reason worth a person's click.
    """
    reasons = {
        "volatile": 0 < finding.seen_in < finding.runs,
        "new": True,
    }
    for name, weight, why in PRIORITIES:
        if reasons[name]:
            return weight, why
    raise AssertionError("'new' is unconditional, so this cannot be reached")


def answered(ledger: Ledger, *, voter: str, sitting: str) -> frozenset[str]:
    """The fingerprints this queue skips, and the one answer that does not count.

    ``needs-evidence`` is not an answer about the finding. The reviewer said
    they could not judge it from what they were shown, and the button says
    "Needs more evidence" -- so treating it as answered took the finding out of
    their queue for good, which is the opposite of what they asked for. It was
    the one input that guaranteed they would never see the finding again.

    So it holds for the sitting it was cast in and no longer: enough to stop the
    finding coming straight back in the same session, and not enough to lose it.
    A later sitting asks again, over whatever evidence exists by then, which is
    what the reviewer was asking for.

    **Public because the app re-asks the same question per request.** It builds
    the queue once and filters it again on every serve, and when that filter was
    a second copy of this rule it was a copy that did not have this paragraph in
    it: it counted a `needs-evidence` answer as answered, and dropped what this
    had just re-offered. One rule needs one reader.
    """
    return frozenset(
        value
        for (value, who), vote in ledger.current().items()
        if (not voter or who == voter)
        and (vote.verdict != "needs-evidence" or vote.sitting == sitting)
    )


def build(
    findings: Iterable[Finding],
    flows_by_case: Mapping[str, FlowMap],
    ledger: Ledger,
    voter: str = "",
    sitting: str = "",
) -> list[QueueItem]:
    """The queue: unanswered findings, most informative first.

    ``voter`` narrows "already answered" to *this* reviewer, which is what makes
    a second opinion possible: leaving it empty skips everything anybody
    answered, and naming a reviewer skips only what they answered themselves.
    ``sitting`` is this session's id, which decides how long a ``needs-evidence``
    answer holds -- see :func:`answered`.

    Deduplicated by fingerprint, keeping the first occurrence. Two runs
    producing one finding is the normal case and is one question, not two.
    :func:`_keyed` is where a finding gets its fingerprint, under its own
    framework's rule.
    """
    skip = answered(ledger, voter=voter, sitting=sitting)

    items: dict[str, QueueItem] = {}
    for value, components, finding in _keyed(findings, flows_by_case):
        if value in skip or value in items:
            continue
        weight, why = priority_of(finding)
        items[value] = QueueItem(
            fingerprint=value,
            components=components,
            finding=finding,
            priority=weight,
            why=why,
        )

    # Sorted by weight, then by case and title, so a queue is stable across
    # rebuilds. A reviewer who steps away mid-sitting comes back to the same
    # order rather than to a reshuffled one.
    return sorted(
        items.values(),
        key=lambda item: (-item.priority, item.finding.case, item.finding.title),
    )


def _keyed(
    findings: Iterable[Finding],
    flows_by_case: Mapping[str, FlowMap],
) -> Iterator[tuple[str, Components, Finding]]:
    """Each finding with its fingerprint, under its own framework's rule.

    One spelling of the keying, because the queue and the merge below have to
    agree on it exactly: a finding counted under one key and asked about under
    another would carry a run count from a different finding.

    **Each finding is keyed by its own framework's rule.**
    :func:`~evals.harness.fingerprint.key_claim` reads
    :data:`~evals.harness.fingerprint.VERSION_FOR` per finding, rather than one
    version being chosen for the whole queue. A sweep carries both packages'
    findings and they do not compose identity the same way; one version over
    both would key an ASVS claim under a rule that reads a verb it never has.

    Both components are offered on every finding and the version keeps what it
    reads, so this loop states no rule of its own about which version wants
    which field.
    """
    for finding in findings:
        value, components = key_claim(
            finding.framework,
            finding.lane,
            finding.element_ids,
            flows_by_case.get(finding.case, {}),
            verb=finding.verb,
            identifier=finding.identifier,
        )
        yield value, components, finding


def merge_runs(
    runs: Sequence[Sequence[Finding]],
    flows_by_case: Mapping[str, FlowMap],
) -> list[Finding]:
    """One finding per identity, carrying how many runs produced it.

    The reading :attr:`QueueItem.volatile` rests on, and the reason the review
    app takes several artifacts rather than one: a finding every sweep of one
    configuration produced is settled, and a finding two sweeps of five
    produced is where a reviewer's click buys the most. Over a single artifact
    every count is 1 of 1 and nothing is volatile, which is the honest reading
    of one run rather than a missing measurement.

    **A run that names one identity twice counts once.** The denominator is
    runs, so a lane repeating itself inside one report is a different question
    from a sweep disagreeing with itself.
    """
    produced_in: Counter[str] = Counter()
    first: dict[str, Finding] = {}
    for findings in runs:
        in_run: dict[str, Finding] = {}
        for value, _, finding in _keyed(findings, flows_by_case):
            in_run.setdefault(value, finding)
        produced_in.update(in_run.keys())
        for value, finding in in_run.items():
            first.setdefault(value, finding)
    return [
        replace(finding, seen_in=produced_in[value], runs=len(runs))
        for value, finding in first.items()
    ]


def summarise(items: Sequence[QueueItem], ledger: Ledger) -> dict[str, Any]:
    """What is left to answer, for a reviewer deciding whether to start.

    ``by_case`` rather than a single total because a sitting is usually one
    case: a reviewer who has fifteen minutes wants to know which case they can
    finish, not that four hundred findings exist somewhere.
    """
    by_case: dict[str, int] = {}
    for item in items:
        by_case[item.finding.case] = by_case.get(item.finding.case, 0) + 1
    return {
        "waiting": len(items),
        "volatile": sum(1 for item in items if item.volatile),
        "by_case": dict(sorted(by_case.items())),
        "votes_recorded": len(ledger),
        "voters": list(ledger.voters()),
        "double_voted": len(ledger.double_voted()),
        "pool": len(ledger.pool()),
    }


def command_review(args: argparse.Namespace) -> int:
    """What a reviewer has waiting, and what the ledger already holds.

    Credential-free, like ``promote`` and ``stability``: it reads a finished
    sweep's reports and ``evals/review/votes/`` and calls nothing. This is
    the read-only half of the loop — ``webapp/review.py`` is where an answer is
    recorded, because a vote wants the source text beside the finding and a
    terminal is the wrong place to read 1,400 characters of prose.

    Prints per case, never one total: a sitting is usually one case, and a
    reviewer with fifteen minutes needs to know which one they can finish.
    """
    from webapp.review import build_session, findings_from_artifacts

    runs, _ = findings_from_artifacts([Path(path) for path in args.artifact])
    session = build_session(runs, args.voter, Path(args.ledger))
    waiting = session.remaining()
    summary = summarise(waiting, ledger.load(Path(args.ledger)))

    print(
        f"{summary['waiting']} findings waiting for {args.voter},"
        f" over {len(runs)} sweep(s)"
    )
    print(f"  {summary['volatile']} found in some runs and not others")
    for case_id, count in summary["by_case"].items():
        print(f"    {case_id:<34} {count}")
    print(
        f"\nledger: {summary['votes_recorded']} votes by"
        f" {', '.join(summary['voters']) or 'nobody'};"
        f" {summary['pool']} findings in the pool;"
        f" {summary['double_voted']} answered twice"
    )
    if waiting:
        print("\nrecord answers with:")
        artifacts = " ".join(f"--artifact {path}" for path in args.artifact)
        print(f"  uv run python webapp/review.py --voter {args.voter} {artifacts}")
    return 0
