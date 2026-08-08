"""What the category agents actually did with ``grounds``, counted.

Three prompt rules govern finding-level attribution and **none of them is
mechanically enforced**, so this module is the only thing that can say whether
the agents follow them:

* ``analyze.md``'s branch rule — the branch follows the trigger and is not
  chosen, so a threat carrying *no quote* is correct rather than defective.
  Read :attr:`CaseGrounds.quoteless_rate` against that: the rule predicts a
  real, non-trivial share of quoteless findings, and a rate near zero is
  evidence the agents are manufacturing quotes to fill a field.
* **One ground per load-bearing fact, no padding** — read
  :attr:`CaseGrounds.grounds_per_threat`, which the rule predicts stays low.
* The verbatim-quote discipline — read :attr:`CaseGrounds.unverified_rate`,
  the share of quote grounds the shipped ladder could not find in the source
  they name.

Credential-free, like :mod:`evals.harness.scorer` and
:mod:`evals.harness.critic_yield`: it takes plain data — the merged drafts and
the report's marks — and computes. Nothing here re-implements the ladder; the
marks it counts are the ones :func:`~stride_service.critic.join_drafts` already
produced with the *shipped* checker, so a sweep cannot grade a normalization
policy the service does not run.

**Some of the numbers only exist on the failure path**, which is why
:class:`GroundsFailure` sits beside the measurement rather than in it. A threat
that loses every ground, an invented evidence reference and a mis-shaped
``Ground`` all kill the job where they are found, so none reaches a report —
measuring them means surviving them per case and counting. All stay Tier 1
failures: a counted case is still a failed case, and the sweep still exits
non-zero.

**Non-gating.** Every rate here is an instrument. No threshold is asserted,
because none has been observed yet — the whole point is the first sweep.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from stride_service.critic import DraftJoinError, GroundsUnverifiedError
from stride_service.evidence import EvidenceResolutionError
from stride_service.report import (
    DraftThreat,
    GroundKind,
    StrideCategory,
    UnverifiedGround,
)

# Why the grounding path killed a case. ``mis-shape`` and ``fail-closed`` are
# the two #91 asks for by name; ``unresolved-evidence`` is an agent naming a
# catalog entry that does not exist, which is the only way its evidence
# selection can fail; ``other`` is every remaining way the fan-in can reject a
# set of drafts — a dangling element reference, a duplicate ID, an unresolvable
# label — kept distinct so no measured rate quietly absorbs a defect that is
# not about grounds at all.
FailureKind = Literal["mis-shape", "fail-closed", "unresolved-evidence", "other"]

_KINDS: tuple[GroundKind, ...] = ("quote", "unknown-attribute", "derived-fact")


@dataclass(frozen=True)
class ThreatGrounds:
    """One draft's grounds: how many, of which branch, and which did not verify.

    The audit trail under the aggregates. ``unverified`` holds indices into the
    draft's own ``grounds`` list, exactly as
    :class:`~stride_service.report.UnverifiedGround` records them, so a
    surprising rate can be walked back to the quote that produced it.
    """

    threat_id: str
    category: StrideCategory
    kinds: tuple[GroundKind, ...]
    unverified: tuple[int, ...]

    @property
    def total(self) -> int:
        return len(self.kinds)

    @property
    def quote_count(self) -> int:
        return self.kinds.count("quote")

    @property
    def has_quote(self) -> bool:
        return "quote" in self.kinds

    def to_json(self) -> dict[str, Any]:
        return {
            "threat_id": self.threat_id,
            "category": self.category,
            "total": self.total,
            "kinds": list(self.kinds),
            "unverified": list(self.unverified),
        }


@dataclass(frozen=True)
class CaseGrounds:
    """One case's grounds measurement, over the drafts the critic was handed.

    The population is the **merged drafts**, not the report's threats, for two
    reasons that agree: the rules being measured are the *category agents'*, so
    a draft the critic later rejected is still evidence about how the agent
    grounded it; and the unverified marks were computed over exactly this set
    at :func:`~stride_service.critic.join_drafts`, so numerator and denominator
    come from one population rather than two.
    """

    case_id: str
    threats: tuple[ThreatGrounds, ...]

    # --- measurement 1: how many grounds, and of which branch ------------

    @property
    def threat_count(self) -> int:
        return len(self.threats)

    @property
    def ground_count(self) -> int:
        return sum(entry.total for entry in self.threats)

    @property
    def kind_counts(self) -> dict[str, int]:
        counts = Counter(kind for entry in self.threats for kind in entry.kinds)
        return {kind: counts.get(kind, 0) for kind in _KINDS}

    @property
    def grounds_per_threat(self) -> float:
        """The padding number: ``analyze.md`` asks for one per load-bearing fact."""
        return ratio(self.ground_count, self.threat_count)

    @property
    def quoteless_count(self) -> int:
        """Threats carrying no quote ground — *correct* under the branch rule."""
        return sum(1 for entry in self.threats if not entry.has_quote)

    @property
    def quoteless_rate(self) -> float:
        return ratio(self.quoteless_count, self.threat_count)

    # --- measurement 2: the unverified-quote rate ------------------------

    @property
    def quote_count(self) -> int:
        return self.kind_counts["quote"]

    @property
    def unverified_count(self) -> int:
        return sum(len(entry.unverified) for entry in self.threats)

    @property
    def unverified_rate(self) -> float:
        """Of the quotes the agents wrote, the share the ladder could not find.

        Denominated in *quotes*, never in grounds: an unknown-attribute ground
        is verified by set membership and can never appear here, so dividing by
        every ground would report a rate that falls whenever the agents happen
        to cite more unknowns.
        """
        return ratio(self.unverified_count, self.quote_count)

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "counts": {
                "threats": self.threat_count,
                "grounds": self.ground_count,
                "quoteless_threats": self.quoteless_count,
                "unverified_quotes": self.unverified_count,
                **self.kind_counts,
            },
            "metrics": {
                "grounds_per_threat": round(self.grounds_per_threat, 3),
                "quoteless_rate": round(self.quoteless_rate, 3),
                "unverified_rate": round(self.unverified_rate, 3),
            },
            "threats": [entry.to_json() for entry in self.threats],
        }


@dataclass(frozen=True)
class GroundsFailure:
    """A case the grounding path killed, recorded as a number rather than a crash.

    ``threat_ids`` and ``draft_count`` are populated only for ``fail-closed``,
    where :class:`~stride_service.critic.GroundsUnverifiedError` carries both
    halves of the rate off the raise site. The other kinds have no comparable
    population — the batch never became drafts, so there is nothing to count
    them against — and each is reported as an occurrence per case.
    """

    case_id: str
    kind: FailureKind
    detail: str
    threat_ids: tuple[str, ...] = ()
    draft_count: int = 0

    @property
    def fail_closed_rate(self) -> float:
        """Share of this case's drafts that lost every ground. 0.0 otherwise."""
        return ratio(len(self.threat_ids), self.draft_count)

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "kind": self.kind,
            "detail": self.detail,
            "threat_ids": list(self.threat_ids),
            "draft_count": self.draft_count,
            "fail_closed_rate": round(self.fail_closed_rate, 3),
        }


def ratio(numerator: float, denominator: float) -> float:
    """Zero denominators are 0.0, never a crash and never a silent 100%."""
    return numerator / denominator if denominator else 0.0


def measure_grounds(
    case_id: str,
    drafts: Sequence[DraftThreat],
    unverified: Iterable[UnverifiedGround],
) -> CaseGrounds:
    """Fold one case's drafts and its unverified marks into one measurement.

    A mark naming a draft that is not here is dropped rather than raised on:
    :class:`~stride_service.report.StrideReport` already refuses to be built
    with a dangling mark, so reaching this function with one means the report
    was never assembled and the sweep has a louder problem than a metric.
    """
    marked: dict[str, set[int]] = {}
    for mark in unverified:
        marked.setdefault(mark.threat_id, set()).add(mark.index)
    return CaseGrounds(
        case_id=case_id,
        threats=tuple(
            ThreatGrounds(
                threat_id=draft.id,
                category=draft.category,
                kinds=tuple(ground.kind for ground in draft.grounds),
                unverified=tuple(sorted(marked.get(draft.id, ()))),
            )
            for draft in drafts
        ),
    )


def classify_failure(case_id: str, error: Exception) -> GroundsFailure:
    """Name what the fan-in rejected, without pattern-matching its prose.

    Three signals, all structural, all type checks or ``loc`` shapes rather
    than message prose. A fail-closed threat arrives as
    :class:`~stride_service.critic.GroundsUnverifiedError`; an invented
    reference as :class:`~stride_service.evidence.EvidenceResolutionError`,
    which is checked first because it is the narrower of two ``DraftJoinError``
    subclasses' siblings; and a mis-shaped ``Ground`` as a
    :class:`pydantic.ValidationError` whose ``loc`` **ends** in
    ``("grounds", <index>)`` with type ``value_error`` — the signature of
    ``Ground._check_shape`` raising.

    THE MIS-SHAPE COUNT IS A TRIPWIRE, AND ITS EXPECTED VALUE IS ZERO. No model
    writes a ``Ground``: an agent selects catalog entries and proposes quotes,
    and :func:`~stride_service.evidence.resolve_proposals` builds the record
    from the entry it looked up. So a mis-shape here is this service
    mis-assembling its own data structure, not an agent misunderstanding a
    prompt, and a non-zero reading is a code defect to be found rather than a
    rate to be tuned. Matched on the ``loc`` tail because the fault can surface
    wherever a draft is revalidated out of session state, not at one fixed
    depth.

    ``unresolved-evidence`` is where an agent's evidence selection actually
    fails now, and it is the number to read against how legible the catalog is:
    an agent can only pick from the closed set it was shown or name something
    that is not in it.

    A *missing* or empty ``grounds`` list is deliberately not a mis-shape: its
    error sits on the list rather than on an entry, and it is a different
    defect from a nonsense field combination.

    Everything else is ``other`` on purpose. Absorbing an unrelated defect into
    a measured rate would make these numbers report faults the grounding path
    never caused.
    """
    if isinstance(error, GroundsUnverifiedError):
        return GroundsFailure(
            case_id=case_id,
            kind="fail-closed",
            detail=str(error),
            threat_ids=error.threat_ids,
            draft_count=error.draft_count,
        )
    if isinstance(error, EvidenceResolutionError):
        return GroundsFailure(
            case_id=case_id, kind="unresolved-evidence", detail=str(error)
        )
    if isinstance(error, ValidationError) and _is_ground_mis_shape(error):
        return GroundsFailure(case_id=case_id, kind="mis-shape", detail=str(error))
    return GroundsFailure(case_id=case_id, kind="other", detail=str(error))


def _is_ground_mis_shape(error: ValidationError) -> bool:
    return any(
        entry["type"] == "value_error"
        and len(entry["loc"]) >= 2
        and entry["loc"][-2] == "grounds"
        and isinstance(entry["loc"][-1], int)
        for entry in error.errors()
    )


# What ``_run_mode`` catches per case: the two exception types a grounds fault
# can arrive as. ``DraftJoinError`` is the fan-in's, and covers evidence
# resolution through :class:`~stride_service.evidence.EvidenceResolutionError`;
# ``ValidationError`` is every schema check the run passes through, including
# the ``output_schema`` ADK applies at each category agent node. Anything else
# still aborts the sweep loudly: a provider timeout is not a measurement.
CAUGHT: tuple[type[Exception], ...] = (DraftJoinError, ValidationError)


def aggregate_grounds(
    measurements: Sequence[CaseGrounds], failures: Sequence[GroundsFailure]
) -> dict[str, Any]:
    """The corpus-wide view, pooled rather than averaged over cases.

    Pooled for the same reason critic yield is: these are small counts per
    case, and a mean of per-case rates would let a case that drafted three
    threats outweigh one that drafted forty.

    The two failure counts are deliberately **cases, not rates**. A case that
    died contributed no denominator to anything — its drafts never reached a
    report — so pooling it into a per-threat rate would divide by a population
    the run does not have. ``fail_closed_threats`` is the one number the raise
    site could preserve, and it is reported as a count beside the cases.
    """
    threats = sum(entry.threat_count for entry in measurements)
    grounds = sum(entry.ground_count for entry in measurements)
    quotes = sum(entry.quote_count for entry in measurements)
    unverified = sum(entry.unverified_count for entry in measurements)
    quoteless = sum(entry.quoteless_count for entry in measurements)
    kinds = Counter()
    for entry in measurements:
        kinds.update(entry.kind_counts)
    by_kind = Counter(failure.kind for failure in failures)
    return {
        "cases": len(measurements),
        "threats": threats,
        "grounds": grounds,
        "quoteless_threats": quoteless,
        "unverified_quotes": unverified,
        **{kind: kinds.get(kind, 0) for kind in _KINDS},
        "grounds_per_threat": round(ratio(grounds, threats), 3),
        "quoteless_rate": round(ratio(quoteless, threats), 3),
        "unverified_rate": round(ratio(unverified, quotes), 3),
        "failed_cases": len(failures),
        "mis_shape_cases": by_kind.get("mis-shape", 0),
        "fail_closed_cases": by_kind.get("fail-closed", 0),
        "unresolved_evidence_cases": by_kind.get("unresolved-evidence", 0),
        "other_failed_cases": by_kind.get("other", 0),
        "fail_closed_threats": sum(
            len(failure.threat_ids)
            for failure in failures
            if failure.kind == "fail-closed"
        ),
    }
