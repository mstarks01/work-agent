"""STRIDE's own record: what this framework judges, on top of a neutral Claim.

A :class:`~analysis_service.report.Claim` carries what the service constructs and
what identifies it. Everything here is what STRIDE *judges* — the category, the
severity band, the countermeasures, and the critic's confidence — which is
exactly the split #163 ruled: a field the report's neutral checks do not read
has no place on the shared shape.

The layering is ``Claim`` -> :class:`DraftThreat` -> :class:`Threat`, where
``Threat`` also inherits :class:`~analysis_service.report.RuledClaim` so that
``verdict`` arrives from the neutral side rather than being declared twice. Both
existing class names survive: renaming them would cost a schema bump and buy
nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, get_args

from pydantic import ConfigDict, Field

from analysis_service.actions import ActionVerb
from analysis_service.report import (
    AnalysisMarks,
    BlockSummary,
    Claim,
    FrameworkAnalysis,
    MissingMitigation,
    Mitigation,
    Proposal,
    ProposalBatch,
    Rating,
    RuledClaim,
    Ruling,
    RulingBatch,
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


def _threat_id(lane: StrideCategory, sequence: int) -> str:
    """One threat ID, composed the way the package's own ``IdRule`` composes it.

    Only :meth:`DraftThreat.lane_diagnostics` needs this: everything on the
    analysis path composes IDs through
    :meth:`~analysis_service.frameworks.FrameworkPackage.compose_id`, and the
    diagnostic runs over drafts whose IDs already exist. Spelled from the same
    two constants, so a message cannot describe an ID shape the resolver does not
    produce.
    """
    return ID_FORMAT.format(prefix=CATEGORY_LETTERS[lane], key=sequence)


#: The lanes each action verb may be filed in. **A table, checked against the
#: corpus**: ``tests/test_stride_lanes_of_verb.py`` fails if any reference claim
#: files a verb in a lane this table does not allow, so the table can never sit
#: narrower than the blessed sets. Twelve verbs take one lane — a flood is
#: denial of service wherever it lands — and eight take several, because the
#: corpus files them so: forging a message is spoofing when the identity is the
#: point and tampering when the content is, and deleting a record is tampering,
#: repudiation or denial of service by what the record was for.
LANES_OF_VERB: dict[str, tuple[StrideCategory, ...]] = {
    "read": ("information-disclosure",),
    "intercept": ("information-disclosure",),
    "elicit": ("information-disclosure",),
    "recover-credential": ("information-disclosure",),
    "guess-credential": ("spoofing",),
    "use-credential": ("spoofing",),
    "impersonate": ("spoofing",),
    "alter-in-transit": ("tampering",),
    "flood": ("denial-of-service",),
    "disable": ("denial-of-service",),
    "escalate": ("elevation-of-privilege",),
    "unattributable": ("repudiation",),
    "abuse-grant": ("elevation-of-privilege", "information-disclosure"),
    "alter": ("tampering", "repudiation", "denial-of-service"),
    "delete": ("tampering", "repudiation", "denial-of-service"),
    "forge": ("spoofing", "tampering"),
    "inject": ("tampering", "elevation-of-privilege", "denial-of-service"),
    "plant": ("tampering", "spoofing", "elevation-of-privilege", "denial-of-service"),
    "replay": ("spoofing", "tampering"),
    "ride-session": ("spoofing", "elevation-of-privilege"),
}


class DraftThreat(Claim):
    """One draft STRIDE finding: a claim plus what this framework judges.

    Everything a category agent's proposal establishes and nothing it may rule
    on — ``verdict`` and ``confidence`` are the critic's, and appear only once a
    draft is promoted to a :class:`Threat`. This is the shape the prompt
    exemplars are lint-parsed against.

    **Built by the service, never emitted by an agent.** An agent answers in
    :class:`ThreatProposal`, which names its evidence rather than serializing
    it, and :func:`~analysis_service.evidence.resolve_proposals` constructs this
    from that answer. So a ``grounds`` list here is code's own output: every
    entry either came out of the evidence catalog whole or is a quote assembled
    from the two fields an agent supplied, and neither route can express a
    mis-shaped :class:`~analysis_service.report.Ground`.

    ``affected_element_ids`` is narrowed to ``min_length=1`` here where the base
    allows it to be empty. Every STRIDE finding is about something in the graph
    — that is what STRIDE-per-element *means* — so a threat naming no element is
    a defect rather than a legal shape for this framework.
    """

    model_config = ConfigDict(extra="forbid")

    category: StrideCategory
    affected_element_ids: list[str] = Field(min_length=1)
    #: Required here where the base allows ``None``. STRIDE's claims are an open
    #: set with no catalog identifier behind them, so two of them are the same
    #: finding when they name one action against one place — and a threat that
    #: names no action leaves the first half of that unanswerable.
    verb: ActionVerb
    severity: Severity
    mitigations: list[Mitigation] = Field(default_factory=list)

    @classmethod
    def claim_marks(cls, drafts: Sequence[Claim]) -> AnalysisMarks:
        """Marks for threats offering no countermeasure and no reason for none.

        The prompt licenses an empty ``mitigations`` for exactly one case — the
        threat is conditional on an ``unknown`` and no countermeasure can be
        named without first learning that fact — and the branch rule makes that
        case recognizable: a threat triggered by an unknown carries an
        ``unknown-attribute`` ground, because the trigger dictates the branch. So
        the licensed empty and the unlicensed one are told apart by the draft's
        own grounds, with no judgement asked of anybody.

        ``absent-attribute`` is deliberately not read here, though it names the
        same two fields. A control the submitter said is *not there* is a fact
        already in hand, and "put the control in" is a countermeasure the agent
        can always name — so a threat resting on one and offering nothing is the
        unlicensed empty, which is exactly what this mark is for.
        """
        return AnalysisMarks(
            missing_mitigations=[
                MissingMitigation(claim_id=draft.id)
                for draft in drafts
                if isinstance(draft, DraftThreat)
                and not draft.mitigations
                and not any(
                    ground.kind == "unknown-attribute" for ground in draft.grounds
                )
            ]
        )

    @classmethod
    def misfiled(cls, draft: Claim) -> str:
        """A draft whose verb no threat in its lane takes, with the lanes that do."""
        if not isinstance(draft, DraftThreat):
            return ""
        lanes = LANES_OF_VERB[draft.verb]
        if draft.category in lanes:
            return ""
        return (
            f"the verb {draft.verb!r} is filed in {draft.category}, and it belongs to"
            f" {' or '.join(lanes)}"
        )

    @classmethod
    def settled_by_grounds(cls, draft: Claim) -> Ruling | None:
        """The neutral ruling, as a :class:`ThreatRuling` rated ``low``.

        The critic's confidence rule already says what a threat resting on an
        ``unknown`` earns, so the ruling code writes carries that rating.
        """
        ruling = super().settled_by_grounds(draft)
        if ruling is None:
            return None
        return ThreatRuling(id=ruling.id, verdict=ruling.verdict, confidence="low")

    @classmethod
    def rating_of(cls, draft: Claim) -> tuple[str, str] | None:
        """This framework grades harm, so a draft's two ratings are its own."""
        if not isinstance(draft, DraftThreat):
            return None
        return draft.severity.likelihood, draft.severity.impact

    @classmethod
    def lane_diagnostics(cls, drafts: Sequence[Claim]) -> list[str]:
        """Every lane whose drafts are not numbered ``01..N``, as messages.

        The prompt asks each agent for "a sequence starting at 1, numbered within
        your lane only". A lane that emits ``S-01, S-02, S-05`` has not followed
        it.

        **Logged rather than raised, and deliberately not on the report.** A gap
        breaks nothing: IDs are opaque handles, they are unique, their letters
        match, and every downstream check passes. Renumbering would make them
        comply, and is refused — rewriting a finding's identity to satisfy a
        cosmetic rule is a bigger liberty than the rule is worth, and it would
        move a threat's ID between two runs of the same input.

        So this is a signal about the *agents*, not about the report, and it goes
        where the other operational facts about a run go rather than to a reader
        who can do nothing with it. Worth having because it is free and because
        nothing else in the graph would ever mention it: an agent quietly
        drifting from its output contract is the kind of thing that is obvious in
        hindsight and invisible in advance.

        **STRIDE's, not the service's**, which is why it is here. "Numbered
        ``01..N``" is a statement about :data:`ID_FORMAT` and a two-digit integer
        key, and :class:`~analysis_service.report.Claim` has neither — ``id`` has no
        shared grammar, so a framework keyed by requirement number has no gaps to
        have. The neutral hook it overrides says nothing at all.
        """
        numbers_by_lane: dict[StrideCategory, list[int]] = {}
        for draft in drafts:
            # Not every Claim is one of ours: the hook's signature is the
            # neutral one, and a caller holding a mixed list is a defect
            # elsewhere rather than something to report from here.
            if isinstance(draft, DraftThreat):
                numbers_by_lane.setdefault(draft.category, []).append(
                    int(draft.id.split("-", 1)[1])
                )
        return [
            f"the {lane} lane numbered its {len(numbers)} drafts"
            f" {', '.join(sorted(_threat_id(lane, n) for n in numbers))},"
            f" not 01..{len(numbers):02d}"
            for lane, numbers in numbers_by_lane.items()
            if sorted(numbers) != list(range(1, len(numbers) + 1))
        ]


class Threat(DraftThreat, RuledClaim):
    """One ruled STRIDE finding, traceable to the elements it affects.

    A draft plus the critic's two judgements. ``verdict`` arrives from
    :class:`~analysis_service.report.RuledClaim` — both parents derive from
    ``Claim``, so Pydantic resolves the field order without ambiguity — and
    ``confidence`` is the one ruling field that is STRIDE's alone.
    """

    confidence: Rating  # critic-calibrated grounding in model facts


class ThreatProposal(Proposal):
    """What a category agent emits: a finding that *names* its evidence.

    The neutral :class:`~analysis_service.report.Proposal` carries the title, the
    description, the element refs and the two evidence lists. STRIDE adds its
    own ID key and its own judgement fields.
    """

    model_config = ConfigDict(extra="forbid")

    # A number, not an ID, and no category beside it. There is one node per
    # STRIDE category and the lane is the graph's fact: an agent restating it is
    # an agent given a constant to contradict, and the ID's letter is a pure
    # function of what it would be restating.
    # :func:`~analysis_service.evidence.resolve_proposals` composes both from the
    # lane it is resolving and this package's ``id_format``.
    #
    # An integer rather than a two-digit string, because a string reintroduces
    # what this removes: ``"1"`` against a ``^\d{2}$`` pattern is a spelling
    # error that fails the node, and a sequence has no spelling.
    sequence: int = Field(ge=1, le=MAX_SEQUENCE)
    affected_element_ids: list[str] = Field(min_length=1)
    # Narrowed with :class:`DraftThreat`'s, and for the reason that class gives:
    # a proposal that validates must resolve into a draft that validates. The
    # agent picks from the closed vocabulary; an unrecognised verb is refused by
    # the response schema before the node returns, not by a check afterwards.
    verb: ActionVerb
    severity: Severity
    mitigations: list[Mitigation] = Field(default_factory=list)


class ThreatProposals(ProposalBatch):
    """What a category agent node emits, narrowed to STRIDE's own proposal.

    The wrapper and its ``claims`` field are
    :class:`~analysis_service.report.ProposalBatch`'s, for the schema-compiler
    reasons that class documents. All this adds is the element type.

    **The field used to be spelled ``threats``**, because the prompt spelled it
    that way and the prompt was STRIDE's. It is not any more: one shared
    ``analyze.md`` asks every registered framework's lane agents for the same
    object, and a second framework filing "threats" would be answering in a word
    its own claims are not. The rename costs one line in that prompt and is
    inside the ``schema_version`` 3.0 break either way.
    """

    model_config = ConfigDict(extra="forbid")

    # A narrowing, which is the point of the wrapper, and which mypy reports as
    # an incompatible override because `list` is invariant. Sound here: these
    # models are built by validation and read, never handed to base-class code
    # that would append a wider element to them.
    claims: list[ThreatProposal]  # type: ignore[assignment]


class ThreatRuling(Ruling):
    """The critic's ruling on one draft, with STRIDE's two extra judgements.

    ``severity`` is the one draft field a ruling may replace, and only where
    the critic's severity-calibration step changed a rating. ``None`` — the
    common case — keeps the agent's rating and justification as written.
    Present, it replaces both together, which is what stops a corrected rating
    from sitting beside a justification that argues for the old one. It is a
    whole :class:`~analysis_service.report.Severity` rather than loose scalars so
    a partial override cannot be expressed.
    """

    model_config = ConfigDict(extra="forbid")

    confidence: Rating
    severity: Severity | None = None


class ThreatRulings(RulingBatch):
    """What the critic and its re-ask emit, narrowed to STRIDE's own ruling.

    Separate from :class:`ThreatProposals` because the element type differs. See
    :class:`~analysis_service.report.ProposalBatch` for why the wrapper exists at
    all and why its field is spelled ``claims`` on both.
    """

    model_config = ConfigDict(extra="forbid")

    claims: list[ThreatRuling]  # type: ignore[assignment]  # narrowed; see ThreatProposals


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

    claims: list[Threat] = Field(default_factory=list)  # type: ignore[assignment]
    rejected_claims: list[Threat] = Field(  # type: ignore[assignment]
        default_factory=list
    )
    summary: StrideSummary
    missing_mitigations: list[MissingMitigation] = Field(default_factory=list)

    @classmethod
    def summarize(cls, claims, rejected_claims) -> StrideSummary:
        """STRIDE's own summary, beside the field that narrowed it."""
        return build_stride_summary(claims, rejected_claims)

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
