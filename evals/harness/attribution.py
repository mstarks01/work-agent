"""Where each ASVS loss happened: one stage per requirement the run got wrong.

The applicability matrix says *which* expected requirements a run missed and
the disposition scorer says *which* it routed wrongly. Neither says *where*
the loss happened, and the audit under #659 asked for exactly that: attribute
every loss to the stage that caused it, using the identities the report already
preserves. A recall figure that falls after a prompt change is a different
event from one that falls after a critic change, and the two instruments above
print the same number for both.

Every fact read here is one the report carries in a closed vocabulary — a
scope state, a rejection cause, a verdict status — so the attribution is a
table over those tokens and reads no prose into any number. A verdict's or a
scope entry's ``reason`` rides on each row for the person who opens the
artifact, and nothing counts it (OWASP LLM01).

**Extraction is not a stage here, by design.** A scored sweep runs in
``analysis`` mode, where the blessed model is injected, so no element can be
missing from the graph. :mod:`evals.harness.modes` measures extraction as its
own mode against that same blessed model; folding the two would put one
failure in two instruments.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from analysis_service.claims import (
    FrameworkAnalysis,
    FrameworkName,
    RuledClaim,
    ScopeEntry,
)
from analysis_service.frameworks.asvs.record import requirement_of
from evals.harness.reference import GoldenCase, ReferenceRequirement

if TYPE_CHECKING:
    from evals.harness.applicability import ApplicabilityScore, DispositionScore

#: This scorer's package, spelled here rather than imported: the applicability
#: module imports this one to build its per-case rows, and a package name is
#: not worth an import cycle.
FRAMEWORK: FrameworkName = "asvs"

#: The stages a loss is charged to. The order is the pipeline's own order, and
#: it is the order the table prints.
Stage = Literal["precondition", "generation", "critic", "verdict", "deferral"]

STAGES: tuple[Stage, ...] = (
    "precondition",
    "generation",
    "critic",
    "verdict",
    "deferral",
)

#: What a scope entry's state says about a requirement the run never applied.
#: Keyed by every state :class:`~analysis_service.claims.ScopeEntry` can carry,
#: and ``tests/test_evals_attribution.py`` holds it to that set, so a fifth
#: state raises here rather than reading as one of these four.
#: ``needs-other-evidence`` cannot be a miss — the matrix counts it applied —
#: so a miss carrying it is a scorer disagreement, and it is charged to the
#: deferral that produced it rather than dropped.
STAGE_FOR_SCOPE: Mapping[str, Stage] = MappingProxyType(
    {
        "undecidable": "precondition",
        "not-applicable": "precondition",
        "not-raised": "generation",
        "needs-other-evidence": "deferral",
    }
)

#: What a wrong routing says about a requirement the run *did* apply. Keyed by
#: the observation kinds :func:`~evals.harness.applicability.observe` can
#: return for an applied requirement. ``rejected`` and ``not-applicable`` are
#: absent on purpose: those requirements are also in the matrix's ``missed``
#: cell, and one loss must move one row.
STAGE_FOR_OBSERVED: Mapping[str, Stage] = MappingProxyType(
    {
        "confirmed": "verdict",
        "needs-info": "verdict",
        "deferred": "deferral",
    }
)


@dataclass(frozen=True)
class Loss:
    """One expected requirement, and the stage that lost it.

    ``cause`` is the closed token the stage was read from — a scope state, a
    rejection step, or an observation kind — so a reader can group rows without
    parsing ``reason``. ``reason`` is the report's own sentence for that row,
    carried for reading and never counted.

    ``re_ask`` is what the *first* critic pass got wrong on this row's own
    claim, from
    :meth:`~analysis_service.claims.FrameworkAnalysis.re_ask_kinds`. Empty is
    the common case and means the ruling that lost the requirement is the one
    the first pass wrote. Non-empty splits a ``critic`` or a ``verdict`` charge
    in two: a rejection the critic argued for is priced against its reasoning,
    and one that arrived after a repair is priced against the first pass and
    ``recritic``. It rides every row, because a re-asked claim can lose at any
    stage that names one.
    """

    requirement: str
    stage: Stage
    cause: str
    reason: str
    must_find: bool
    expected: str
    re_ask: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "stage": self.stage,
            "cause": self.cause,
            "reason": self.reason,
            "must_find": self.must_find,
            "expected": self.expected,
            "re_ask": list(self.re_ask),
        }


@dataclass(frozen=True)
class CaseAttribution:
    """Every loss one case charged, and the count per stage."""

    case: str
    losses: tuple[Loss, ...]

    @property
    def by_stage(self) -> Counter[str]:
        return Counter(loss.stage for loss in self.losses)

    @property
    def re_asked_by_stage(self) -> Counter[str]:
        """The subset of :attr:`by_stage` whose claim the first pass fumbled."""
        return Counter(loss.stage for loss in self.losses if loss.re_ask)

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "framework": FRAMEWORK,
            "losses": [loss.to_json() for loss in self.losses],
            "by_stage": {stage: self.by_stage[stage] for stage in STAGES},
            "re_asked_by_stage": {
                stage: self.re_asked_by_stage[stage] for stage in STAGES
            },
        }


def _by_requirement(claims: Sequence[RuledClaim]) -> dict[str, RuledClaim]:
    """One array's claims by the requirement each names; the first one wins.

    Handed ``rejected_claims``, this keeps every rejection and not only the
    ruling ones: a draft sent back for its ``reasoning``, its lane or as a
    duplicate leaves its requirement unruled, and the scope then lists it
    ``not-raised`` — but a lane *did* raise it, so the loss belongs to the
    critic and not to generation. Reading the scope alone would charge the
    wrong stage, which is the mistake the block's own ``not-raised`` docstring
    warns of.
    """
    keyed: dict[str, RuledClaim] = {}
    for claim in claims:
        requirement = requirement_of(claim.id)
        if requirement:
            keyed.setdefault(requirement, claim)
    return keyed


def _references(case: GoldenCase) -> dict[str, ReferenceRequirement]:
    return {
        reference.requirement: reference
        for reference in case.references.get(FRAMEWORK) or ()
        if isinstance(reference, ReferenceRequirement)
    }


def _charge_miss(
    requirement: str,
    rejections: Mapping[str, RuledClaim],
    scope: Mapping[str, ScopeEntry],
) -> tuple[Stage, str, str]:
    """The stage, cause and reason for a requirement the run never applied."""
    rejection = rejections.get(requirement)
    if rejection is not None:
        step = rejection.verdict.rejected_because or "evidence"
        return "critic", step, rejection.verdict.reason
    entry = scope.get(requirement)
    if entry is not None:
        return STAGE_FOR_SCOPE[entry.state], entry.state, entry.reason
    # Every unit appears, by the block's own contract; a requirement with
    # neither a claim nor an entry is the block's defect, and it is charged
    # where the reader will look first.
    return "generation", "unlisted", ""


def attribute_case(
    case: GoldenCase,
    block: FrameworkAnalysis,
    matrix: ApplicabilityScore,
    routing: DispositionScore,
) -> CaseAttribution:
    """Charge each of one case's losses to a stage. No model call.

    Handed the matrix and the routing score the sweep already computed, and
    reads the ``missed`` cell of one and the ``wrong`` list of the other, so
    this cannot disagree with the two numbers it explains about which
    requirements were lost.
    """
    references = _references(case)
    applied = _by_requirement(block.claims)
    rejections = _by_requirement(block.rejected_claims)
    scope = {entry.unit: entry for entry in block.scope}
    losses: list[Loss] = []

    def re_ask(requirement: str) -> tuple[str, ...]:
        """What the first critic pass got wrong on this requirement's claim.

        Keyed by the claim's own ID rather than by the requirement, because
        that is what the mark names. A requirement with no claim in either
        array — lost before any lane drafted it — has no ruling to charge, and
        the empty answer says so.
        """
        claim = applied.get(requirement) or rejections.get(requirement)
        return () if claim is None else block.re_ask_kinds(claim.id)

    for requirement in matrix.missed:
        reference = references[requirement]
        stage, cause, reason = _charge_miss(requirement, rejections, scope)
        losses.append(
            Loss(
                requirement,
                stage,
                cause,
                reason,
                reference.must_find,
                reference.disposition or "",
                re_ask(requirement),
            )
        )

    for judged in routing.wrong:
        routed = STAGE_FOR_OBSERVED.get(judged.observed.kind)
        if routed is None:
            continue
        reference = references[judged.requirement]
        # A verdict row reads the claim's own reason; a deferral row reads the
        # scope entry's, because the report carries no claim for it.
        claim = applied.get(judged.requirement)
        entry = scope.get(judged.requirement)
        reason = claim.verdict.reason if claim is not None else ""
        if entry is not None:
            reason = entry.reason
        losses.append(
            Loss(
                judged.requirement,
                routed,
                judged.observed.kind,
                reason,
                reference.must_find,
                judged.expected,
                re_ask(judged.requirement),
            )
        )

    return CaseAttribution(case.id, tuple(losses))


def pooled(rows: Sequence[CaseAttribution]) -> Mapping[str, Any]:
    """Losses per stage over the corpus, counted rather than averaged."""
    totals: Counter[str] = Counter()
    must_find: Counter[str] = Counter()
    re_asked: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    for row in rows:
        totals.update(row.by_stage)
        must_find.update(loss.stage for loss in row.losses if loss.must_find)
        re_asked.update(row.re_asked_by_stage)
        for loss in row.losses:
            kinds.update(loss.re_ask)
    return {
        "cases": len(rows),
        "losses": sum(totals.values()),
        "by_stage": {stage: totals[stage] for stage in STAGES},
        "must_find_by_stage": {stage: must_find[stage] for stage in STAGES},
        # The subset of each stage's losses whose ruling came out of a re-ask,
        # and which problems the first pass had on those claims. Counted per
        # loss and per kind rather than summed together: one claim carries
        # more than one kind, so the kinds do not add up to the losses.
        "re_asked_by_stage": {stage: re_asked[stage] for stage in STAGES},
        "re_asked": sum(re_asked.values()),
        "by_re_ask_kind": dict(sorted(kinds.items())),
    }


def render(rows: Sequence[CaseAttribution]) -> None:
    """One line per case, one column per stage, then the pooled counts."""
    if not rows:
        return
    print("\nASVS loss attribution (which stage lost each expected requirement)")
    print(f"{'case':<26} " + " ".join(f"{stage:>12}" for stage in STAGES))
    for row in rows:
        counts = row.by_stage
        print(f"{row.case:<26} " + " ".join(f"{counts[stage]:>12}" for stage in STAGES))
    totals = pooled(rows)
    print(
        f"pooled over {totals['cases']} cases: {totals['losses']} losses, "
        + ", ".join(f"{stage} {totals['by_stage'][stage]}" for stage in STAGES)
        + " (instrument, non-gating)"
    )
    if totals["re_asked"]:
        print(
            f"  of those, {totals['re_asked']} lost a claim the first critic"
            " pass fumbled: "
            + ", ".join(
                f"{stage} {totals['re_asked_by_stage'][stage]}"
                for stage in STAGES
                if totals["re_asked_by_stage"][stage]
            )
            + " — first-pass problems: "
            + ", ".join(
                f"{kind} {count}" for kind, count in totals["by_re_ask_kind"].items()
            )
        )


def artifact(rows: Sequence[CaseAttribution]) -> dict[str, Any]:
    return {
        "attribution": [row.to_json() for row in rows],
        "attribution_aggregate": pooled(rows) if rows else None,
    }
