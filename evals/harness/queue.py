"""Which findings a reviewer is asked about, and in what order.

Human attention is the scarce resource in this whole design, so the order is a
budget rather than a convenience. A finding twenty runs already agree on buys
nothing; a finding that appears in one run of five, or that two configurations
disagree about, buys the most information a single click can.

**Built from finished artifacts and the ledger, and nothing else.** No provider,
no credentials, no engine. That is what lets a sitting happen on a laptop with
the repository checked out, and it is what makes the queue reproducible: the
same artifacts and the same ledger produce the same queue in the same order.

**Blind by construction.** :class:`QueueItem` carries no model name, no tier and
no configuration label — those live on the :class:`~evals.harness.ledger.Vote`
and are stamped after the answer. A reviewer who can see which configuration
produced a finding is a reviewer whose vote can encode a preference about the
configuration, which is exactly the bias the vote exists to escape.

**Skips what is already answered.** A vote is spent once and kept forever
because it hangs on a fingerprint, so the second sitting over a corpus sees only
what the first did not: new findings from a changed configuration, and
disagreements. That is the whole economic argument for the fingerprint.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from evals.harness.fingerprint import (
    Components,
    components_for,
    fingerprint,
    version_for,
)
from evals.harness.identity import FlowMap
from evals.harness.ledger import Ledger
from evals.harness.verbs import GLOSS, family_of
from stride_service.report import FrameworkName


@dataclass(frozen=True)
class Finding:
    """One produced claim, reduced to what a reviewer and the queue read.

    Deliberately not a :class:`~stride_service.report.Claim`: a queue item must
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
        return {
            "fingerprint": self.fingerprint,
            "components": self.components.to_json(),
            "case": self.finding.case,
            "framework": self.finding.framework,
            "lane": self.finding.lane,
            "title": self.finding.title,
            "description": self.finding.description,
            "element_ids": list(self.finding.element_ids),
            "quotes": list(self.finding.quotes),
            "verb": self.finding.verb,
            "verb_gloss": GLOSS.get(self.finding.verb or "", ""),
            "verb_family": (family_of(self.finding.verb) if self.finding.verb else ""),
            "seen_in": self.finding.seen_in,
            "runs": self.finding.runs,
            "priority": self.priority,
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
        "unmatched",
        20,
        (
            "no reference set carries this, so nothing scores it either way until"
            " somebody says whether it is real"
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


def priority_of(finding: Finding, in_reference_set: bool) -> tuple[int, str]:
    """The first reason that applies, with its weight — never the sum.

    Summing would rank a finding that is merely new-and-unmatched above a
    volatile one, and volatility is the reason worth a person's click.
    """
    reasons = {
        "volatile": 0 < finding.seen_in < finding.runs,
        "unmatched": not in_reference_set,
        "new": True,
    }
    for name, weight, why in PRIORITIES:
        if reasons[name]:
            return weight, why
    raise AssertionError("'new' is unconditional, so this cannot be reached")


def build(
    findings: Iterable[Finding],
    flows_by_case: Mapping[str, FlowMap],
    ledger: Ledger,
    reference_pool: frozenset[str] = frozenset(),
    voter: str = "",
) -> list[QueueItem]:
    """The queue: unanswered findings, most informative first.

    ``voter`` narrows "already answered" to *this* reviewer, which is what makes
    a second opinion possible: leaving it empty skips everything anybody
    answered, and naming a reviewer skips only what they answered themselves.

    Deduplicated by fingerprint, keeping the first occurrence. Two runs
    producing one finding is the normal case and is one question, not two.
    :func:`_keyed` is where a finding gets its fingerprint, under its own
    framework's rule.
    """
    if voter:
        answered = frozenset(key[0] for key in ledger.current() if key[1] == voter)
    else:
        answered = ledger.voted_fingerprints()

    items: dict[str, QueueItem] = {}
    for value, components, finding in _keyed(findings, flows_by_case):
        if value in answered or value in items:
            continue
        weight, why = priority_of(finding, value in reference_pool)
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

    **Each finding is keyed by its own framework's rule**, from
    :data:`~evals.harness.fingerprint.VERSION_FOR`, rather than by one version
    chosen for the whole queue. A sweep carries both packages' findings and they
    do not compose identity the same way; one version over both would key an
    ASVS claim under a rule that reads a verb it never has.
    """
    for finding in findings:
        version = version_for(finding.framework)
        components = components_for(
            finding.framework,
            finding.lane,
            finding.element_ids,
            flows_by_case.get(finding.case, {}),
            verb=finding.verb if version >= 2 else None,
        )
        yield fingerprint(components, version=version), components, finding


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
