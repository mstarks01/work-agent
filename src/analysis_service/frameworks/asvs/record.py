"""ASVS's own record: a ruling on applicability, on top of a neutral Claim.

An ASVS claim never reports a pass. The standard's own verification needs access
to documentation, source code, configuration and the development team. A job
here carries prose about a system, so the pass half of the pass-or-fail decision
is not reachable from the input. The three neutral **Verdict** states carry the
question this package can answer:

``confirmed``
    the requirement applies to this system, and the input does not show it
    satisfied.
``needs-info``
    the requirement applies, and the input does not settle it.
``rejected``
    the critic rules that the requirement does not apply.

There is no fourth state reaching for a pass. The package states that decision
rather than hiding a defect, and ``disclaimer.md`` states it to the reader of
every report.

The record grades nothing: no severity, no confidence and no mitigations. Two
consequences follow from the package contract. The package carries no
``severity_rubric.md``, because the gate refuses a rubric beside a record that
grades nothing. And ``affected_element_ids`` stays empty on the many claims that
address a coding practice, which is legal on the neutral
:class:`~analysis_service.report.Claim`, and is the reason the base widened it.

The layering is ``Claim``, then :class:`DraftRequirementRuling`, then
:class:`RequirementRuling`, which is the same shape STRIDE's record uses.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from analysis_service.frameworks.asvs.catalog import (
    ASVS_VERSION,
    CHAPTER_NUMBERS,
    LANES,
    AsvsLevel,
    requirement_id,
    requirements_for,
)
from analysis_service.frameworks.asvs.rules import ruled_out_requirements
from analysis_service.report import (
    MAX_CLAIMS_PER_BATCH,
    BlockSummary,
    Claim,
    FrameworkAnalysis,
    Proposal,
    ProposalBatch,
    RuledClaim,
    Ruling,
    RulingBatch,
    ScopeEntry,
    build_block_summary,
)
from analysis_service.system_model import SystemModel

__all__ = [
    "ASVS_ID_FORMAT",
    "AsvsAnalysis",
    "AsvsChapter",
    "AsvsOptions",
    "AsvsSummary",
    "DraftRequirementRuling",
    "RequirementProposal",
    "RequirementProposals",
    "RequirementRuling",
    "RequirementRulings",
    "build_asvs_summary",
    "requirement_of",
]

#: One lane, spelled where a type checker can read it. The catalog is the source
#: of truth and this restates it, so the two are checked against each other at
#: import: a chapter the catalog adds and this omits would give the report a
#: field that cannot hold its own lane.
AsvsChapter = Literal[
    "encoding-and-sanitization",
    "validation-and-business-logic",
    "web-frontend-security",
    "api-and-web-service",
    "file-handling",
    "authentication",
    "session-management",
    "authorization",
    "self-contained-tokens",
    "oauth-and-oidc",
    "cryptography",
    "secure-communication",
    "configuration",
    "data-protection",
    "secure-coding-and-architecture",
    "security-logging-and-error-handling",
    "webrtc",
]

if set(get_args(AsvsChapter)) != set(LANES):
    raise ValueError(
        f"AsvsChapter names {sorted(get_args(AsvsChapter))}, but the catalog"
        f" declares {sorted(LANES)}"
    )

#: The ``str.format`` template this package's IDs are composed from. It is the
#: standard's own version-safe reference — ``v5.0.0-1.2.5`` — so a claim cites
#: against the published standard without a reader composing anything.
#:
#: The version is baked in rather than templated, because a claim ID is a string
#: a reader cites and the standard spells its own reference this way. The pair
#: ``(framework, framework_version)`` on every claim carries the same value, and
#: the package gate has nothing to say about either; what keeps them in step is
#: that both read :data:`~analysis_service.frameworks.asvs.catalog.ASVS_VERSION`.
ASVS_ID_FORMAT = f"v{ASVS_VERSION}-{{prefix}}.{{key}}"

#: What a lane agent supplies as its ID key: the ``<section>.<requirement>`` pair
#: inside a chapter. The chapter is the graph's fact and the agent never spells
#: it, exactly as a STRIDE agent never spells its category.
REQUIREMENT_KEY_PATTERN = r"^\d{1,2}\.\d{1,2}$"

_ID_PREFIX = f"v{ASVS_VERSION}-"


def requirement_of(claim_id: str) -> str:
    """The standard's identifier inside one composed claim ID, or ``""``.

    ``v5.0.0-1.2.5`` gives ``V1.2.5``. The empty string for anything else, which
    is what lets a block's own checks report a malformed ID rather than raise on
    one: a report payload is validated from outside this build too.
    """
    if not claim_id.startswith(_ID_PREFIX):
        return ""
    return f"V{claim_id[len(_ID_PREFIX) :]}"


class AsvsOptions(BaseModel):
    """The job-level values ASVS needs: the level, and nothing else.

    **No default, and the package gate checks that.** ASVS 5.0 broke the tie
    between application risk and level and tells the organization to choose, so
    nothing in a **Valid System Model** picks one. A job that omits the level
    rejects on the input ladder, which is what stops one install inventing a
    level another install would not.

    ``extra="forbid"`` means an option a caller invented is refused there too,
    rather than ignored.

    Here beside the record rather than beside the package, because the block
    reads it back: :meth:`AsvsAnalysis.scope_entries` resolves a job's options
    through this model, so what a level *is* is declared once.
    """

    model_config = ConfigDict(extra="forbid")

    level: AsvsLevel


class DraftRequirementRuling(Claim):
    """One draft ASVS ruling: a claim plus the chapter it was reached in.

    Everything a lane agent's proposal establishes and nothing the critic rules
    on. The chapter is the one field this framework adds, and the graph stamps it
    from the lane it is resolving rather than from anything an agent said.

    **Built by the service, never emitted by an agent**, on the same route
    STRIDE's drafts take: an agent answers in :class:`RequirementProposal`, and
    :func:`~analysis_service.evidence.resolve_proposals` constructs this from that
    answer.
    """

    model_config = ConfigDict(extra="forbid")

    chapter: AsvsChapter
    # The claim side of the pair narrowed on the proposal above, and it has to
    # move with it: a proposal that validates must resolve into a claim that
    # validates, which is the rule `Proposal.verb`'s own comment states.
    verb: SkipJsonSchema[None] = None

    @classmethod
    def ruled_out(
        cls, model: SystemModel, options: Mapping[str, Any], lane: str
    ) -> dict[str, str]:
        """The chapter's requirements at the level, where its deciding test fired nowhere."""
        level = AsvsOptions.model_validate(options).level
        return ruled_out_requirements(model, level, lane)

    @classmethod
    def unit_of(cls, draft: Claim) -> str:
        """The requirement a draft rules on, which is this framework's unit."""
        return requirement_of(draft.id)

    @classmethod
    def partition_proposals(
        cls, proposals: Sequence[Any], lane: str, carried: Collection[str]
    ) -> tuple[list[Any], dict[str, str]]:
        """Every requirement whose lane agent said this job cannot settle it.

        The agent names the kind of evidence that would settle the requirement;
        this drops the ones that kind is not among what the job carries. A
        ``prose`` answer is kept, because a job carrying prose *can* settle it —
        the description was simply thin, and that is a request the submitter can
        act on rather than a wall.

        The reason names the kind, so the **Scope Entry** tells a reader what
        would answer the requirement instead of only that nothing did.
        """
        defer = [
            proposal
            for proposal in proposals
            if proposal.needs_evidence and proposal.needs_evidence not in carried
        ]
        kept = [proposal for proposal in proposals if proposal not in defer]
        return kept, {
            requirement_id(lane, proposal.requirement): proposal.needs_evidence
            for proposal in defer
        }


class RequirementRuling(DraftRequirementRuling, RuledClaim):
    """One ruled ASVS finding: a draft plus the critic's verdict.

    ``verdict`` arrives from :class:`~analysis_service.report.RuledClaim` and is the
    only thing the critic adds. STRIDE's ``confidence`` has no counterpart here:
    a framework that grades nothing has no rating to calibrate.
    """


class RequirementProposal(Proposal):
    """What a lane agent emits: a ruling that *names* its evidence.

    The neutral :class:`~analysis_service.report.Proposal` carries the title, the
    description, the element refs and the two evidence lists. ASVS adds its ID
    key and nothing else, because it judges nothing else.

    ``affected_element_ids`` keeps the base's empty default rather than STRIDE's
    ``min_length=1``. Most ASVS requirements address a coding practice with no
    position in the graph, so a requirement naming no element is the ordinary
    case here rather than a defect.

    ``needs_evidence`` splits one of the three cases the output contract already
    asks a lane agent to decide between. Two of them the agent can answer from
    the input — the requirement does not apply, or it applies and the input
    shows a gap. The third, *it applies and the input does not settle it*, hides
    two different answers: **send more description**, and **no description will
    ever answer this**. Only the agent that read the Source can tell them apart,
    so it is asked, and it answers by naming the kind of evidence that would
    settle the requirement.

    Empty means the agent ruled: the claim stands and the **Critic** judges it.
    ``prose`` means more of the same input would settle it, so the claim also
    stands and reads as a request the submitter can act on. Any other kind means
    this job cannot reach it at all, and the requirement becomes a **Scope
    Entry** rather than a claim.

    **Judgement, deliberately, rather than a table.** Whether a description
    settles a requirement depends on what that description says: four
    requirements were ruled ``confirmed`` from prose only because the submission
    happened to describe CORS and to state that nothing rate-limited a caller.
    A table keyed by requirement answers a question that has no fixed answer,
    and would be wrong invisibly. This is wrong visibly, in a field a reader can
    disagree with.
    """

    model_config = ConfigDict(extra="forbid")

    # NARROWED CLOSED, AND OFF THE PROVIDER SCHEMA. A verb is half of what makes
    # two claims of an open set the same finding; this package's claims carry a
    # catalog requirement and compose their identity from that and the place, so
    # a verb here is a field nothing reads. :class:`~analysis_service.report.Claim`
    # says as much and left it optional, which is not the same as forbidden.
    #
    # A live sweep found 42 of 960 claims carrying one — `replay`, `impersonate`,
    # `abuse-grant` — and 30 of those in the OAuth lane alone. That is not noise
    # in a spare field: an agent reaching for an attacker action while ruling on
    # a requirement is answering a different framework's question, and the verb
    # is the visible half of it.
    #
    # ``SkipJsonSchema`` on the precedent :class:`~analysis_service.report.Severity`
    # sets: an agent is never handed the field, so it cannot fill it, and a value
    # arriving any other way is refused rather than carried.
    verb: SkipJsonSchema[None] = None
    requirement: str = Field(pattern=REQUIREMENT_KEY_PATTERN, max_length=10)
    # REQUIRED, WITH NO DEFAULT, AND THAT IS THE WHOLE MECHANISM. A structured
    # output model omits a field that carries a default, and a prompt alone does
    # not oblige it to answer; a required field does.
    #
    # `""` is a legal value — it is the ordinary answer, meaning the agent
    # ruled — but it must be *chosen* rather than fallen into. The enum is
    # what keeps a required field from becoming an invented one.
    needs_evidence: Literal["", "prose", "code", "config", "people"]


class RequirementProposals(ProposalBatch):
    """What an ASVS lane agent node emits, narrowed to this package's proposal."""

    model_config = ConfigDict(extra="forbid")

    # A narrowing, which is the point of the wrapper; `list` is invariant, so
    # mypy reports the override. Sound here for the reason STRIDE's is: these
    # models are built by validation and read, never appended to.
    claims: list[RequirementProposal] = Field(  # type: ignore[assignment]
        max_length=MAX_CLAIMS_PER_BATCH
    )


class RequirementRulingProposal(Ruling):
    """The critic's ruling on one draft, with nothing of this package's own.

    STRIDE's ruling may replace a draft's severity. ASVS has no draft field a
    ruling could replace, so this adds nothing to the neutral shape and exists
    only so the wrapper below has an element type to narrow to.
    """

    model_config = ConfigDict(extra="forbid")


class RequirementRulings(RulingBatch):
    """What the ASVS critic and its re-ask emit."""

    model_config = ConfigDict(extra="forbid")

    claims: list[RequirementRulingProposal]  # type: ignore[assignment]


class AsvsSummary(BlockSummary):
    """The neutral counts plus the one breakdown this record can be cut by.

    ``by_severity`` has no counterpart: a framework that grades nothing would
    carry a map that can only ever be empty.
    """

    by_chapter: dict[AsvsChapter, int] = Field(default_factory=dict)


def build_asvs_summary(
    claims: Sequence[RequirementRuling],
    rejected_claims: Sequence[RequirementRuling],
) -> AsvsSummary:
    """Compute ASVS's block summary mechanically from the block's contents."""
    neutral = build_block_summary(claims, rejected_claims)
    by_chapter: dict[AsvsChapter, int] = {}
    for claim in claims:
        by_chapter[claim.chapter] = by_chapter.get(claim.chapter, 0) + 1
    return AsvsSummary(**neutral.model_dump(), by_chapter=by_chapter)


def _scope_state(
    unit: str,
    refusal_reason: str,
    deferred: Mapping[str, str],
    ruled_out: Mapping[str, str] = MappingProxyType({}),
) -> Literal["applicable", "not-applicable", "needs-other-evidence"]:
    """Which of the three states one unlisted requirement is in.

    Order matters. A refused **Precondition** rules out every requirement, so it
    wins over anything a lane said — a lane that never ran cannot have deferred
    anything, and if the two ever disagreed the precondition is the older and
    broader fact. After that a deferred requirement is the one this job could
    not settle, and everything else was considered and raised nothing.
    """
    if refusal_reason or unit in ruled_out:
        return "not-applicable"
    return "needs-other-evidence" if unit in deferred else "applicable"


class AsvsAnalysis(FrameworkAnalysis):
    """The report block one ASVS analysis fills.

    ``level`` is the one field ASVS adds to the neutral block, and it makes the
    block self-contained. A reader holding this block alone can tell which
    requirement set produced the answer, and the block's own checks can verify
    that every requirement in that set appears exactly once. The value arrives
    from the job's own options, which the report also carries on ``job``.

    **The block reports no pass and says so.** ``disclaimer`` carries the
    package's own text, and the word compliance appears in neither: a
    level-filtered run rules on the cumulative set the standard defines for that
    level and on nothing above it, which is a partial verification and not a
    compliance result, and this service rules applicability rather than
    conformance.
    """

    claims: list[RequirementRuling] = Field(default_factory=list)  # type: ignore[assignment]
    rejected_claims: list[RequirementRuling] = Field(  # type: ignore[assignment]
        default_factory=list
    )
    summary: AsvsSummary
    level: AsvsLevel

    @classmethod
    def summarize(cls, claims, rejected_claims) -> AsvsSummary:
        """ASVS's own summary, beside the fields that narrowed it."""
        return build_asvs_summary(claims, rejected_claims)

    @classmethod
    def scope_entries(
        cls,
        *,
        lanes: Sequence[str],
        claims: Sequence[RuledClaim],
        options: Mapping[str, Any],
        refusal_reason: str,
        deferred: Mapping[str, str] = MappingProxyType({}),
        ruled_out: Mapping[str, str] = MappingProxyType({}),
    ) -> list[ScopeEntry]:
        """Every requirement in the selected level this block raised no claim about.

        **The unit is a requirement, not a lane.** The neutral default answers in
        lanes because a lane is the only unit the service knows without reading a
        catalog it does not own. ASVS owns one, so it answers in the standard's
        own units — which is also what the standard asks of a report: "some
        requirements may be non-applicable, and this must be noted".

        Two shapes, by whether the precondition let the lanes run:

        * refused — every requirement in the level is ``not-applicable``, and the
          reason is the precondition's own.
        * satisfied — every requirement no claim covers is ``applicable``, which
          reads as *considered and nothing raised* rather than as *ruled out*.
        """
        del lanes
        # Through the package's own options model rather than by reading the key,
        # so the one declaration of what a level is decides here too.
        level = AsvsOptions.model_validate(options).level
        ruled = {requirement_of(claim.id) for claim in claims if claim.rules_on_unit()}
        return [
            ScopeEntry(
                unit=requirement.id,
                state=_scope_state(requirement.id, refusal_reason, deferred, ruled_out),
                reason=refusal_reason
                or ruled_out.get(requirement.id, "")
                or (
                    f"applies, and settling it needs {deferred[requirement.id]}"
                    if requirement.id in deferred
                    else ""
                ),
                needs=(
                    ""
                    if refusal_reason or requirement.id in ruled_out
                    else deferred.get(requirement.id, "")
                ),
            )
            for requirement in requirements_for(level)
            if refusal_reason or requirement.id not in ruled
        ]

    def block_issues(self, known_element_ids):
        """The neutral checks, plus this block's own two.

        The completeness check is what makes *every requirement appears*
        mechanical rather than editorial: every requirement in the selected level
        is either ruled on by a claim or listed in ``scope``. It runs on the
        block alone, because ``level`` is on the block.

        **Every issue here is fatal, so every issue here must be the service's
        own.** An issue on this list raises out of the report validator and costs
        the whole job — after every node has been paid for — so a check that an
        *agent* can trip does not belong on it. That is the line ADR 0009 drew
        for a bad evidence reference and the one STRIDE's lane numbering already
        sits behind: an agent's slip costs its entry, never the run. See
        :meth:`_level_coverage_issues` for the one this rules out.
        """
        return [
            *super().block_issues(known_element_ids),
            *self._summary_breakdown_issues(),
            *self._chapter_agreement_issues(),
            *self._level_coverage_issues(),
        ]

    def _summary_breakdown_issues(self) -> list[str]:
        """The one breakdown is this block's own claims, recounted."""
        recount = build_asvs_summary(self.claims, self.rejected_claims)
        if self.summary.by_chapter == recount.by_chapter:
            return []
        return ["summary.by_chapter does not match the asvs analysis's own contents"]

    def _chapter_agreement_issues(self) -> list[str]:
        """A claim's ID and its ``chapter`` field name the same chapter.

        **Fatal because it is the service's own, and it is the one this block
        can be sure of.** Both values come from one call:
        :func:`~analysis_service.evidence.resolve_proposals` composes the ID from
        the lane's chapter number and stamps ``chapter`` from the same lane, so
        an agent cannot separate them and a disagreement here can only mean this
        code got it wrong. That is exactly the standing
        :meth:`block_issues` requires, and it is why this sits beside the
        coverage check rather than being dropped and marked like an unknown
        requirement is.

        Worth checking rather than trusting, because a report payload is
        validated from outside this build too: the package's own comment says
        the chapter appears in the ID, in the lane and in the record "from one
        call, so the three cannot disagree", and until now nothing read that
        back. A claim whose ID says chapter 11 and whose record says
        ``authentication`` files a cryptography ruling under the wrong lane's
        summary and the wrong chapter's coverage.

        A claim whose ID does not resolve at all is left alone; that is what
        ``_level_coverage_issues`` and the identity checks above already see.
        """
        issues = []
        for claim in (*self.claims, *self.rejected_claims):
            requirement = requirement_of(claim.id)
            if not requirement:
                continue
            # Indexed rather than fetched with a default: `chapter` is a
            # closed Literal, and this module refuses to import unless it names
            # exactly the catalog's lanes, so a miss here is impossible and a
            # branch for one was unreachable.
            expected = CHAPTER_NUMBERS[claim.chapter]
            if requirement.split(".")[0] != f"V{expected}":
                issues.append(
                    f"claim {claim.id!r} resolves to {requirement} but its"
                    f" chapter field says {claim.chapter!r} (V{expected})"
                )
        return issues

    def _level_coverage_issues(self) -> list[str]:
        """Every requirement in the level appears once, and ``scope`` holds no other.

        **Only what the service builds is checked.** ``scope`` is composed by
        :meth:`scope_entries` from the catalog and the claims, so each finding
        below can only mean this code got it wrong — which is what makes them
        safe to raise on.

        **A claim naming a requirement outside the level is deliberately not an
        issue.** It is the one thing on this block a lane agent can get wrong on
        its own: the prompt asks it to rule on its chapter at the level the scope
        line names, and an agent that reaches one requirement further has filed a
        real ruling about a real requirement. Failing the report would throw away
        that finding and the other 16 lanes' work with it, to enforce a boundary
        the reader can already see — ``level`` is on the block and the selection
        is on the job. So the claim rides, and the level still says what was
        asked for.
        """
        expected = {req.id for req in requirements_for(self.level)}
        every = (*self.claims, *self.rejected_claims)
        named = {requirement_of(claim.id) for claim in every}
        # Appearing and being answered are two facts. A draft the critic sent
        # back for its lane or as a duplicate still appears, in the audit array,
        # and answers nothing — so its requirement is listed in ``scope`` as
        # well, and that pair is not a duplication. A block written before the
        # cause was read lists no such requirement, and still appears once.
        ruled = {requirement_of(claim.id) for claim in every if claim.rules_on_unit()}
        listed = {entry.unit for entry in self.scope}
        issues = []
        missing = sorted(expected - named - listed)
        if missing:
            issues.append(
                f"level {self.level} requirements appear in neither the claims nor"
                f" scope: {missing}"
            )
        both = sorted(ruled & listed)
        if both:
            issues.append(f"requirements appear in both the claims and scope: {both}")
        stray = sorted(listed - expected)
        if stray:
            issues.append(
                f"scope names requirements outside level {self.level}: {stray}"
            )
        return issues
