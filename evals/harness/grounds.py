"""What the lane agents actually did with ``grounds``, counted.

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
marks it counts are the ones :func:`~analysis_service.critic.join_drafts` already
produced with the *shipped* checker, so a sweep cannot grade a normalization
policy the service does not run.

* **The repair rung** — read :attr:`CaseGrounds.repaired_count`, the quotes
  the ladder refused and :func:`~analysis_service.grounding.repair_quote` then
  rewrote to the source's nearest span. This is the number that moves
  :data:`~analysis_service.grounding.REPAIR_THRESHOLD`.
* **A claim that lost every ground** — read :attr:`CaseGrounds.dropped_count`,
  the claims the service dropped and marked because nothing they cited held:
  every quote absent from its source, or every reference outside the catalog.

**One number still exists only on the failure path**, which is why
:class:`GroundsFailure` sits beside the measurement rather than in it. The
fan-in still kills a job over a dangling element reference, a duplicate ID or
an unresolvable source label, so measuring those means surviving them per case
and counting. A counted case is still a failed case, and the sweep still exits
non-zero.

A mis-shaped ``Ground`` is the one thing here that is **not** measured, because
it is not an agent behaviour any more (:class:`GroundMisShape`).

**Non-gating.** Every rate here is an instrument. No threshold is asserted,
because none has been observed yet — the whole point is the first sweep.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from analysis_service.critic import DraftJoinError
from analysis_service.frameworks import PACKAGES
from analysis_service.report import (
    Claim,
    DroppedClaim,
    FrameworkName,
    GroundKind,
    RepairedQuote,
    UnverifiedGround,
)

# Why the fan-in killed a case. A claim that loses every ground costs its own
# entry rather than the case: it is dropped and marked, and
# :attr:`CaseGrounds.dropped_count` reads it off the report. So one kind is
# left, ``other``: every way the fan-in still rejects a set of drafts (a
# dangling element reference, a duplicate ID, an unresolvable label). It is
# kept as a kind of its own so no measured rate quietly absorbs a defect that
# is not about grounds at all.
#
# A mis-shaped ``Ground`` is deliberately **not** among them: see
# :class:`GroundMisShape`.
FailureKind = Literal["other"]

_KINDS: tuple[GroundKind, ...] = (
    "quote",
    "unknown-attribute",
    "absent-attribute",
    "derived-fact",
)


@dataclass(frozen=True)
class ThreatGrounds:
    """One draft's grounds: how many, of which branch, and which did not verify.

    The audit trail under the aggregates. ``unverified`` holds indices into the
    draft's own ``grounds`` list, exactly as
    :class:`~analysis_service.report.UnverifiedGround` records them, so a
    surprising rate can be walked back to the quote that produced it.
    """

    threat_id: str
    framework: FrameworkName
    #: The lane the claim was found in, read off whichever field this package's
    #: ``IdRule`` stamps it into — ``category`` for STRIDE, ``chapter`` for ASVS.
    #: Empty for a package whose record carries no lane, which is legal.
    lane: str
    kinds: tuple[GroundKind, ...]
    unverified: tuple[int, ...]
    #: Indices of quote grounds the repair rung rewrote, the same way.
    repaired: tuple[int, ...] = ()

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
            "framework": self.framework,
            "lane": self.lane,
            "total": self.total,
            "kinds": list(self.kinds),
            "unverified": list(self.unverified),
            "repaired": list(self.repaired),
        }


@dataclass(frozen=True)
class CaseGrounds:
    """One case's grounds measurement, over the drafts the critic was handed.

    The population is the **merged drafts**, not the report's threats, for two
    reasons that agree: the rules being measured are the *category agents'*, so
    a draft the critic later rejected is still evidence about how the agent
    grounded it; and the unverified marks were computed over exactly this set
    at :func:`~analysis_service.critic.join_drafts`, so numerator and denominator
    come from one population rather than two.
    """

    case_id: str
    framework: FrameworkName
    threats: tuple[ThreatGrounds, ...]
    #: The claims this block dropped for losing every ground, as the report
    #: marks them. Not in ``threats``: a dropped claim never reached the critic.
    dropped: tuple[DroppedClaim, ...] = ()

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

    @property
    def repaired_count(self) -> int:
        return sum(len(entry.repaired) for entry in self.threats)

    @property
    def repaired_rate(self) -> float:
        """Of the quotes the agents wrote, the share the repair rung rewrote."""
        return ratio(self.repaired_count, self.quote_count)

    # --- measurement 3: claims dropped for losing every ground -----------
    @property
    def dropped_count(self) -> int:
        return len(self.dropped)

    @property
    def dropped_rate(self) -> float:
        """Of every claim the lanes drafted, the share the service dropped.

        Denominated in drafts plus drops: a dropped claim is not in ``threats``,
        so the population is what the agents wrote rather than what survived.
        """
        return ratio(self.dropped_count, self.threat_count + self.dropped_count)

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "framework": self.framework,
            "counts": {
                "threats": self.threat_count,
                "grounds": self.ground_count,
                "quoteless_threats": self.quoteless_count,
                "unverified_quotes": self.unverified_count,
                "repaired_quotes": self.repaired_count,
                "dropped_claims": self.dropped_count,
                **self.kind_counts,
            },
            "metrics": {
                "grounds_per_threat": round(self.grounds_per_threat, 3),
                "quoteless_rate": round(self.quoteless_rate, 3),
                "unverified_rate": round(self.unverified_rate, 3),
                "repaired_rate": round(self.repaired_rate, 3),
                "dropped_rate": round(self.dropped_rate, 3),
            },
            "threats": [entry.to_json() for entry in self.threats],
            "dropped": [mark.model_dump(mode="json") for mark in self.dropped],
        }


class GroundMisShape(RuntimeError):
    """A ``Ground`` this service assembled wrongly. Never an agent's doing.

    THE OTHER #91 RATE USED TO LIVE HERE, AND ITS EXPECTED VALUE IS NOW ZERO.
    No model writes a ``Ground``: a category agent selects catalog entries and
    proposes quotes, and :func:`~analysis_service.evidence.resolve_proposals`
    builds the record out of the entry it looked up. So a mis-shape is this
    service mis-assembling its own data structure, and there is no agent
    behaviour behind it to have a rate.

    It therefore **ends the sweep** rather than being counted and survived. The
    counted kinds are things models do, and a number is the right answer to
    each; this is a code defect, and continuing would pool measurements taken
    from a build that is known to be broken. Same rule the module already
    applies to a provider timeout: not everything that can go wrong during a
    measurement is a measurement.
    """


@dataclass(frozen=True)
class GroundsFailure:
    """A case the fan-in killed, recorded as a number rather than a crash.

    The batch never became drafts, so there is no population to count it
    against; it is reported as an occurrence per case.
    """

    case_id: str
    kind: FailureKind
    detail: str

    def to_json(self) -> dict[str, Any]:
        return {"case": self.case_id, "kind": self.kind, "detail": self.detail}


def ratio(numerator: float, denominator: float) -> float:
    """Zero denominators are 0.0, never a crash and never a silent 100%."""
    return numerator / denominator if denominator else 0.0


def lane_of(framework: FrameworkName, draft: Claim) -> str:
    """The lane a draft was found in, without naming any framework's field.

    A package declares which record field its ``IdRule`` stamps the lane into,
    so this reads the declaration rather than a field name. A package whose
    record carries no lane answers the empty string, which is a legal shape
    (``lane_field`` is ``None``) rather than a defect to raise on.
    """
    field = PACKAGES[framework].id_rule.lane_field
    return str(getattr(draft, field, "")) if field else ""


def measure_grounds(
    case_id: str,
    framework: FrameworkName,
    drafts: Sequence[Claim],
    unverified: Iterable[UnverifiedGround],
    dropped: Iterable[DroppedClaim] = (),
    repaired: Iterable[RepairedQuote] = (),
) -> CaseGrounds:
    """Fold one framework's drafts and its unverified marks into one measurement.

    **Per framework, over that framework's own drafts and its own block's
    marks.** ADR 0002 exempts no package from finding-level attribution, so
    every one of them has a quoteless and an unverified rate; pooling two
    packages' drafts here would put one number over two populations whose agents
    were given different instructions.

    Typed against the neutral :class:`~analysis_service.report.Claim` because that
    is what carries ``grounds``. Nothing here reads a field a package declares.

    A mark naming a draft that is not here is dropped rather than raised on:
    the report already refuses to be built with a dangling mark, so reaching
    this function with one means the report was never assembled and the sweep
    has a louder problem than a metric.
    """
    marked: dict[str, set[int]] = {}
    for mark in unverified:
        marked.setdefault(mark.claim_id, set()).add(mark.index)
    rewritten: dict[str, set[int]] = {}
    for repair in repaired:
        rewritten.setdefault(repair.claim_id, set()).add(repair.index)
    return CaseGrounds(
        case_id=case_id,
        framework=framework,
        threats=tuple(
            ThreatGrounds(
                threat_id=draft.id,
                framework=framework,
                lane=lane_of(framework, draft),
                kinds=tuple(ground.kind for ground in draft.grounds),
                unverified=tuple(sorted(marked.get(draft.id, ()))),
                repaired=tuple(sorted(rewritten.get(draft.id, ()))),
            )
            for draft in drafts
        ),
        dropped=tuple(dropped),
    )


def classify_failure(case_id: str, error: Exception) -> GroundsFailure:
    """Name what the fan-in rejected, without pattern-matching its prose.

    Structural signals only, all type checks or ``loc`` shapes rather than
    message prose. A claim that lost every ground is not a failure any more —
    it is dropped and marked, and :func:`measure_grounds` counts it.

    **Raises rather than classifies** on a mis-shaped ``Ground``, recognised as
    a :class:`pydantic.ValidationError` whose ``loc`` **ends** in
    ``("grounds", <index>)`` with type ``value_error`` — the signature of
    ``Ground._check_shape``. That is not a kind of case failure, it is
    :class:`GroundMisShape`, and the reason is that no agent can produce one.
    Matched on the ``loc`` tail rather than a fixed path because a draft is
    revalidated wherever it is read back out of session state, which nests the
    same fault at different depths.

    A *missing* or empty ``grounds`` list is deliberately not a mis-shape: its error
    sits on the list rather than on an entry, it is reachable from a proposal
    that named no evidence, and it is a different defect from a nonsense field
    combination.

    Everything else is ``other`` on purpose. Absorbing an unrelated defect into
    a measured rate would make these numbers report faults the grounding path
    never caused.
    """
    if isinstance(error, ValidationError) and _is_ground_mis_shape(error):
        raise GroundMisShape(
            "a Ground reached validation mis-shaped, which no agent can cause:"
            " every Ground is built by resolve_proposals out of a catalog entry."
            f" This is a defect in this build, not a measurement. {error}"
        ) from error
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
# can arrive as. ``DraftJoinError`` is the fan-in's; ``ValidationError`` is
# every schema check the run passes through, including
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

    The failure count is deliberately **cases, not a rate**. A case that died
    contributed no denominator to anything — its drafts never reached a report
    — so pooling it into a per-threat rate would divide by a population the run
    does not have.
    """
    threats = sum(entry.threat_count for entry in measurements)
    grounds = sum(entry.ground_count for entry in measurements)
    quotes = sum(entry.quote_count for entry in measurements)
    unverified = sum(entry.unverified_count for entry in measurements)
    repaired = sum(entry.repaired_count for entry in measurements)
    quoteless = sum(entry.quoteless_count for entry in measurements)
    dropped = sum(entry.dropped_count for entry in measurements)
    kinds: Counter[str] = Counter()
    for entry in measurements:
        kinds.update(entry.kind_counts)
    return {
        "cases": len(measurements),
        "threats": threats,
        "grounds": grounds,
        "quoteless_threats": quoteless,
        "unverified_quotes": unverified,
        "repaired_quotes": repaired,
        "dropped_claims": dropped,
        **{kind: kinds.get(kind, 0) for kind in _KINDS},
        "grounds_per_threat": round(ratio(grounds, threats), 3),
        "quoteless_rate": round(ratio(quoteless, threats), 3),
        "unverified_rate": round(ratio(unverified, quotes), 3),
        "repaired_rate": round(ratio(repaired, quotes), 3),
        "dropped_rate": round(ratio(dropped, threats + dropped), 3),
        "failed_cases": len(failures),
    }


def render(
    measurements: Sequence[CaseGrounds], failures: Sequence[GroundsFailure]
) -> None:
    """The three measurements, printed with the branch mix beside the rates.

    ``quoteless`` is on the same line as the rates and is not a fault:
    ``analyze.md``'s branch rule predicts a real share of findings whose
    trigger was an unknown or a crossing rather than the submitter's words.
    Read low with suspicion, not high.
    """
    for entry in measurements:
        counts = entry.kind_counts
        print(
            f"{entry.case_id:<26} grounds {entry.ground_count}"
            f" on {entry.threat_count} threats ({entry.grounds_per_threat:.2f} ea)"
            f"  q/u/a/d {counts['quote']}/{counts['unknown-attribute']}"
            f"/{counts['absent-attribute']}/{counts['derived-fact']}"
            f"  quoteless {entry.quoteless_rate:.0%}"
            f"  unverified {entry.unverified_count}/{entry.quote_count}"
            f"  repaired {entry.repaired_count}"
            f"  dropped {entry.dropped_count}"
        )
    for failure in failures:
        print(f"{failure.case_id:<26} grounds FAILED ({failure.kind})")
    if measurements or failures:
        totals = aggregate_grounds(measurements, failures)
        print(
            f"grounds: {totals['grounds_per_threat']:.2f} per threat,"
            f" quoteless {totals['quoteless_rate']:.0%},"
            f" unverified {totals['unverified_rate']:.1%},"
            f" repaired {totals['repaired_rate']:.1%},"
            f" dropped {totals['dropped_rate']:.1%},"
            f" failed cases {totals['failed_cases']}"
            " (instrument, non-gating)"
        )


def artifact(
    measurements: Sequence[CaseGrounds], failures: Sequence[GroundsFailure]
) -> dict[str, Any]:
    """This instrument's artifact keys.

    The aggregate is ``None`` rather than a fold over nothing: a sweep that
    measured no case and a sweep whose every rate came out zero are different
    findings, and one shape for both would hide the first.
    """
    return {
        "grounds": [entry.to_json() for entry in measurements],
        "grounds_failures": [failure.to_json() for failure in failures],
        "grounds_aggregate": (
            aggregate_grounds(measurements, failures)
            if measurements or failures
            else None
        ),
    }
