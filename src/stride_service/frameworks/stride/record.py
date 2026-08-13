"""STRIDE's own record: what this framework judges, on top of a neutral Claim.

A :class:`~stride_service.report.Claim` carries what the service constructs and
what identifies it. Everything here is what STRIDE *judges* — the category, the
severity band, the countermeasures, and the critic's confidence — which is
exactly the split #163 ruled: a field the report's neutral checks do not read
has no place on the shared shape.

The layering is ``Claim`` -> :class:`DraftThreat` -> :class:`Threat`, where
``Threat`` also inherits :class:`~stride_service.report.RuledClaim` so that
``verdict`` arrives from the neutral side rather than being declared twice. Both
existing class names survive: renaming them would cost a schema bump and buy
nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from stride_service.report import (
    BlockSummary,
    Claim,
    FrameworkAnalysis,
    MissingMitigation,
    Mitigation,
    Proposal,
    Rating,
    RuledClaim,
    Ruling,
    Severity,
    SeverityLevel,
    build_block_summary,
    derive_severity_level,
)

__all__ = [
    "CATEGORY_LETTERS",
    "STRIDE_CATEGORIES",
    "DraftThreat",
    "StrideAnalysis",
    "StrideCategory",
    "StrideSummary",
    "Threat",
    "ThreatProposal",
    "ThreatProposals",
    "ThreatRuling",
    "ThreatRulings",
    "build_stride_summary",
]

#: This package's own ruleset version — the 12 candidate rules, the 6 lanes and
#: the text under ``frameworks/stride/``. STRIDE is not a published standard with
#: releases, so the value names **this repo's** ruleset rather than anyone
#: else's. It is required and non-empty on every claim for the reason a
#: framework identifier alone is uninterpretable one release later; a fixed
#: constant that never moved would assert nothing, so this moves when the rules,
#: the lanes or the lane text change in a way that changes what a claim means.
STRIDE_VERSION = "1.0"

StrideCategory = Literal[
    "spoofing",
    "tampering",
    "repudiation",
    "information-disclosure",
    "denial-of-service",
    "elevation-of-privilege",
]

# The six categories in canonical STRIDE order, derived from the type itself
# so the two can never drift. They are this package's ``lanes`` member.
STRIDE_CATEGORIES: tuple[StrideCategory, ...] = get_args(StrideCategory)

# One lane's ID prefix. Threat IDs are <category letter>-<per-lane sequence>,
# e.g. "S-01", and the service composes them from this table plus the package's
# ``id_format``. **Data, never code**: under one neutral resolver a composed ID
# cannot carry the wrong letter, so the check that used to enforce the pairing
# has nothing left to check and is gone.
CATEGORY_LETTERS: dict[StrideCategory, str] = {
    "spoofing": "S",
    "tampering": "T",
    "repudiation": "R",
    "information-disclosure": "I",
    "denial-of-service": "D",
    "elevation-of-privilege": "E",
}

#: The ``str.format`` template this package's IDs are composed from, with the
#: lane's prefix and the proposal's own key. Repo-authored and never
#: caller-supplied, so no agent value walks an attribute through ``format``.
#:
#: The two-digit field caps a lane at 99 findings, and that cap is **STRIDE's
#: own** rather than a fact about every framework: ``id`` has no shared grammar,
#: so a second package does not inherit it, and raising it is a separate
#: decision about this format string.
ID_FORMAT = "{prefix}-{key:02d}"

#: The largest ``sequence`` :data:`ID_FORMAT` can render without widening. Sits
#: here beside the format it is a property of, and bounds
#: :class:`ThreatProposal`'s own field.
MAX_SEQUENCE = 99


class DraftThreat(Claim):
    """One draft STRIDE finding: a claim plus what this framework judges.

    Everything a category agent's proposal establishes and nothing it may rule
    on — ``verdict`` and ``confidence`` are the critic's, and appear only once a
    draft is promoted to a :class:`Threat`. This is the shape the prompt
    exemplars are lint-parsed against.

    **Built by the service, never emitted by an agent.** An agent answers in
    :class:`ThreatProposal`, which names its evidence rather than serializing
    it, and :func:`~stride_service.evidence.resolve_proposals` constructs this
    from that answer. So a ``grounds`` list here is code's own output: every
    entry either came out of the evidence catalog whole or is a quote assembled
    from the two fields an agent supplied, and neither route can express a
    mis-shaped :class:`~stride_service.report.Ground`.

    ``affected_element_ids`` is narrowed to ``min_length=1`` here where the base
    allows it to be empty. Every STRIDE finding is about something in the graph
    — that is what STRIDE-per-element *means* — so a threat naming no element is
    a defect rather than a legal shape for this framework.
    """

    model_config = ConfigDict(extra="forbid")

    category: StrideCategory
    affected_element_ids: list[str] = Field(min_length=1)
    severity: Severity
    mitigations: list[Mitigation] = Field(default_factory=list)


class Threat(DraftThreat, RuledClaim):
    """One ruled STRIDE finding, traceable to the elements it affects.

    A draft plus the critic's two judgements. ``verdict`` arrives from
    :class:`~stride_service.report.RuledClaim` — both parents derive from
    ``Claim``, so Pydantic resolves the field order without ambiguity — and
    ``confidence`` is the one ruling field that is STRIDE's alone.
    """

    confidence: Rating  # critic-calibrated grounding in model facts


class ThreatProposal(Proposal):
    """What a category agent emits: a finding that *names* its evidence.

    The neutral :class:`~stride_service.report.Proposal` carries the title, the
    description, the element refs and the two evidence lists. STRIDE adds its
    own ID key and its own judgement fields.
    """

    model_config = ConfigDict(extra="forbid")

    # A number, not an ID, and no category beside it. There is one node per
    # STRIDE category and the lane is the graph's fact: an agent restating it is
    # an agent given a constant to contradict, and the ID's letter is a pure
    # function of what it would be restating.
    # :func:`~stride_service.evidence.resolve_proposals` composes both from the
    # lane it is resolving and this package's ``id_format``.
    #
    # An integer rather than a two-digit string, because a string reintroduces
    # what this removes: ``"1"`` against a ``^\d{2}$`` pattern is a spelling
    # error that fails the node, and a sequence has no spelling.
    sequence: int = Field(ge=1, le=MAX_SEQUENCE)
    affected_element_ids: list[str] = Field(min_length=1)
    severity: Severity
    mitigations: list[Mitigation] = Field(default_factory=list)


class ThreatProposals(BaseModel):
    """What a category agent node emits: an object wrapping its list of proposals.

    The wrapper exists for the *schema*, not for the domain. A node's
    ``output_schema`` is what the graph asks the provider to constrain
    generation to, and a bare ``list[ThreatProposal]`` cannot be asked for: ADK
    cannot convert a generic alias into a response format, so it sends none and
    the node generates unconstrained — silently, with only a log line. Wrapping
    the list in a model gives the conversion something it can carry, and an
    object root at that, which is what OpenAI's structured outputs require and
    a bare array would not satisfy.

    Nothing downstream sees it: the graph unwraps at the node boundary, so the
    domain keeps working in lists.

    ``threats`` rather than ``proposals`` because the field name is the agent's
    output contract and the prompt has always spelled it that way.
    """

    model_config = ConfigDict(extra="forbid")

    threats: list[ThreatProposal]


class ThreatRuling(Ruling):
    """The critic's ruling on one draft, with STRIDE's two extra judgements.

    ``severity`` is the one draft field a ruling may replace, and only where
    the critic's severity-calibration step changed a rating. ``None`` — the
    common case — keeps the agent's rating and justification as written.
    Present, it replaces both together, which is what stops a corrected rating
    from sitting beside a justification that argues for the old one. It is a
    whole :class:`~stride_service.report.Severity` rather than loose scalars so
    a partial override cannot be expressed.
    """

    model_config = ConfigDict(extra="forbid")

    confidence: Rating
    severity: Severity | None = None


class ThreatRulings(BaseModel):
    """What the critic and its re-ask emit: the wrapper over one ruling per draft.

    Separate from :class:`ThreatProposals` because the element type differs. See
    that class for why the wrapper exists at all, and why its field is still
    spelled ``threats`` on both: it is the shape the provider constrains
    generation to, not the domain's word for what is inside.
    """

    model_config = ConfigDict(extra="forbid")

    threats: list[ThreatRuling]


class StrideSummary(BlockSummary):
    """The neutral counts plus the two breakdowns STRIDE's record can be cut by.

    A framework that grades nothing declares neither of these, which is why they
    are here rather than on the base: a ``by_severity`` map over claims with no
    severity is a field that can only ever be empty.
    """

    by_category: dict[StrideCategory, int] = Field(default_factory=dict)
    by_severity: dict[SeverityLevel, int] = Field(default_factory=dict)


def build_stride_summary(
    threats: Sequence[Threat], rejected_threats: Sequence[Threat]
) -> StrideSummary:
    """Compute STRIDE's block summary mechanically from the block's contents.

    The three neutral counts come from :func:`build_block_summary`, so the
    envelope's per-block recount and this cannot disagree about them.
    """
    neutral = build_block_summary(threats, rejected_threats)
    by_category: dict[StrideCategory, int] = {}
    by_severity: dict[SeverityLevel, int] = {}
    for threat in threats:
        by_category[threat.category] = by_category.get(threat.category, 0) + 1
        level = derive_severity_level(
            threat.severity.likelihood, threat.severity.impact
        )
        by_severity[level] = by_severity.get(level, 0) + 1
    return StrideSummary(
        **neutral.model_dump(), by_category=by_category, by_severity=by_severity
    )


class StrideAnalysis(FrameworkAnalysis):
    """The report block one STRIDE analysis fills.

    Narrows the two claim arrays to :class:`Threat` and the summary to
    :class:`StrideSummary`, and adds the one mark that is about a STRIDE
    judgement rather than about a claim's evidence: a threat offering no
    countermeasure and no reason for offering none. A framework that recommends
    nothing has no such mark to carry, which is why it is here and not on the
    base.
    """

    claims: list[Threat] = Field(default_factory=list)
    rejected_claims: list[Threat] = Field(default_factory=list)
    summary: StrideSummary
    missing_mitigations: list[MissingMitigation] = Field(default_factory=list)

    def block_issues(self, known_element_ids):
        """The neutral checks, plus STRIDE's own mark placement."""
        return [
            *super().block_issues(known_element_ids),
            *self._claim_mark_issues(self.missing_mitigations, "missing mitigation"),
            *self._summary_breakdown_issues(),
        ]

    def _summary_breakdown_issues(self) -> list[str]:
        """The two breakdowns are this block's own claims, recounted."""
        recount = build_stride_summary(self.claims, self.rejected_claims)
        return [
            f"summary.{field} does not match the stride analysis's own contents"
            for field in ("by_category", "by_severity")
            if getattr(self.summary, field) != getattr(recount, field)
        ]
