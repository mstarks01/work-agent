"""Mechanical checks at the join and assemble seams around a framework's critic.

This is the deterministic half of the critic step. Mechanical checks belong in
code, and prompts carry only judgement. Everything here is a check no model
should be asked to perform: that a framework's lane agents' drafts cite elements
the System Model contains, that claim IDs are unique, that every grounds entry
resolves, that every quote ground is really in the source it names, and that the
critic ruled on exactly the drafts it was given, each ruling carrying a
well-formed verdict. A package's critic prompt names these as already done, so
its judgement is spent on evidence, lanes, duplicates and whatever else that
framework grades. For grounds, it is spent on the one question code cannot
answer: whether a quote that is verbatim actually supports the finding it was
filed under.

The checks are neutral, and there is one seam per framework rather than one
across frameworks. Every check here reads
:class:`~analysis_service.report.Claim`,
:class:`~analysis_service.report.Ruling` and the package contract, so a second
framework's output goes through the same code. What it never does is merge two
frameworks' drafts. The join runs per package, in that package's own declared
lane order, because two frameworks' claims are not comparable and a duplicate
across them is not a duplicate.

The assemble seam is also where a ruling becomes a claim. A critic emits
judgements keyed by draft ID rather than the drafts themselves, as a
:class:`~analysis_service.report.Ruling`. The agent's own fields therefore reach
the report from the copy this service already holds, rather than round-tripping
through a model that was never asked to change them.

Model output is untrusted input (OWASP LLM05), and the service validates it here
before anything reaches the report. Both seams fail closed, and list every issue
at once. An agent that hallucinates an element ID, or a critic that drops
claims, is a defect to surface loudly rather than to paper over by discarding
the offending entries.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import NamedTuple, get_args

from analysis_service.frameworks import FrameworkPackage, FrameworkSchemas
from analysis_service.grounding import (
    PreparedSource,
    deadline_spent,
    normalize,
    prepare_source,
    repair_deadline,
    repair_prepared,
    verify_normalized,
)
from analysis_service.references import canonical, snap
from analysis_service.report import (
    BEYOND_GROUNDS,
    DROPPED_REASON_MAX_CHARS,
    ELEMENT_REF_MAX_CHARS,
    MENTION_MAX_CHARS,
    AnalysisMarks,
    Claim,
    DroppedClaim,
    Ground,
    RepairedQuote,
    RuledClaim,
    Ruling,
    SeverityLevel,
    UnknownRef,
    UnresolvedMention,
    UnresolvedReference,
    UnverifiedGround,
    Verdict,
)
from analysis_service.system_model import (
    DataFlow,
    Element,
    ModelIndex,
    SystemModel,
    TrustBoundary,
)

# Most severe first — the order a graded framework's ``claims`` array carries.
# The service holds the order because it holds
# :data:`~analysis_service.report.SeverityLevel`; whether a framework grades at all
# is its record's business, and one that does not is ordered by ID alone.
SEVERITY_ORDER: tuple[SeverityLevel, ...] = ("critical", "high", "medium", "low")


class DraftJoinError(ValueError):
    """One framework's merged lane agents' drafts fail a mechanical check."""


class CriticOutputError(ValueError):
    """The critic's output does not account for exactly the drafts it saw."""


class AssembledClaims(NamedTuple):
    """One critic's ruled claims, split into that block's two arrays."""

    claims: list[RuledClaim]
    rejected_claims: list[RuledClaim]


class JoinedDrafts(NamedTuple):
    """What the fan-in produces: the merged drafts, and what did not check out.

    Two returns rather than one because they have different owners. The drafts
    are the agents'; the :class:`~analysis_service.report.AnalysisMarks` are the
    *service's* record of what each draft failed to make good on — a quote that
    is not in the source it named, an element ID a description cited that does
    not exist, and whatever the package's own record adds
    (:meth:`~analysis_service.report.Claim.claim_marks`). They ride beside the
    drafts rather than on them, because a field an agent could set about its own
    accuracy is not evidence of it.

    None of the marks is fatal, and that is the whole policy of this seam:
    checks that decide whether a finding *means* anything fail closed, and
    checks that describe how complete it is are recorded for a reader. The
    fan-in has no re-ask path, so the second kind must never cost a report.
    """

    drafts: list[Claim]
    marks: AnalysisMarks


# An element ID as it appears inside prose. Flows carry a second segment and
# nothing else does, so the two shapes are spelled separately rather than as one
# optional group that would read ``store:orders-db:anything`` as a store.
#
# Built from the element classes' own ``id_prefix``, so a sixth element type
# joins this pattern by existing rather than by someone remembering. Matched
# case-insensitively and snapped afterwards, for the reason every other
# reference in this module is: the spelling is not the claim.
_FLOW_PREFIX = DataFlow.id_prefix
_OTHER_PREFIXES = sorted(
    element.id_prefix for element in get_args(Element) if element is not DataFlow
)
_SLUG = r"[a-z0-9-]+"
_MENTION_RE = re.compile(
    rf"\b(?:{_FLOW_PREFIX}:{_SLUG}:{_SLUG}|(?:{'|'.join(_OTHER_PREFIXES)}):{_SLUG})",
    re.IGNORECASE,
)


def mentioned_ids(description: str) -> list[str]:
    """Every element ID a description names in prose, in the order written.

    Deliberately narrow: a token has to open with one of the five real type
    prefixes and a colon, which is a shape ordinary English does not produce —
    ``"Process: the web app"`` has a space and does not match. The cost of that
    narrowness is a miss rather than a false alarm, which is the right way
    round for a check whose output annotates a finding a human will read.

    Measured over the 18 hand-authored descriptions in STRIDE's own lane
    exemplars, the closest thing the repo holds to real agent prose: **24 distinct IDs
    extracted, 0 of them spurious** — every token found is one of the two
    exemplar systems' 24 real element IDs, and all 24 are found. Small, and the
    only corpus of threat descriptions that exists; enough to say the pattern
    reads prose without inventing citations in it.

    Trailing hyphens are trimmed because prose runs an ID into an em-dash
    substitute more often than a real slug ends in one; ``normalize_name``
    strips them, so no legal ID ends in a hyphen anyway.
    """
    return [match.group().rstrip("-") for match in _MENTION_RE.finditer(description)]


def _unresolved_mentions(
    claims: Iterable[Claim], element_ids: Collection[str]
) -> list[UnresolvedMention]:
    """Marks for every ID a description cites that the model does not contain."""
    return [
        UnresolvedMention(claim_id=claim.id, mention=mention[:MENTION_MAX_CHARS])
        for claim in claims
        for mention in mentioned_ids(claim.description)
        if not canonical(mention, element_ids)
    ]


# The two branches whose reference is an element and an attribute of it. They
# differ in what they say about that attribute — never stated against stated
# absent — and in nothing this module does: both snap the same field and both
# resolve against the same model, so every check here reads the pair.
_ATTRIBUTE_KINDS = frozenset({"unknown-attribute", "absent-attribute"})


def _snapped_ground(
    ground: Ground, element_ids: Collection[str], labels: Collection[str]
) -> Ground:
    """One grounds entry with its branch's reference in canonical spelling."""
    if ground.kind == "quote":
        # Only when the job carries labels at all: the in-process engine drives a
        # hand-authored model with none, and an empty known set snaps nothing.
        if not labels:
            return ground
        return ground.model_copy(
            update={"source_label": snap(ground.source_label, labels)}
        )
    if ground.kind in _ATTRIBUTE_KINDS:
        return ground.model_copy(
            update={"element_id": snap(ground.element_id, element_ids)}
        )
    if ground.kind == "derived-fact":
        return ground.model_copy(update={"flow_id": snap(ground.flow_id, element_ids)})
    # An absent-element ground carries a term rather than an ID, so there is no
    # spelling to canonicalise: the model names it nowhere, which is the point.
    return ground


def snap_drafts(
    drafts: Iterable[Claim],
    element_ids: Collection[str],
    source_labels: Collection[str] = (),
) -> list[Claim]:
    """Every reference a draft carries, in the spelling the job holds.

    Run at the fan-in before the checks, so what those checks compare — and what
    the report goes on to carry — is the canonical spelling rather than whatever
    each of a framework's independently-vendored lane agents happened to type.
    The drafts reach the report unaltered otherwise: this rewrites references and
    nothing else.

    ``element_ids`` covers flow references too, because a flow *is* an element
    and its ID is in the same set. Whether that flow is a derived boundary
    crossing is a different question, asked afterwards by the check that owns it.
    """
    return [
        draft.model_copy(
            update={
                "affected_element_ids": [
                    snap(ref, element_ids) for ref in draft.affected_element_ids
                ],
                "grounds": [
                    _snapped_ground(ground, element_ids, source_labels)
                    for ground in draft.grounds
                ],
            }
        )
        for draft in drafts
    ]


def snap_rulings(
    rulings: Iterable[Ruling], element_ids: Collection[str]
) -> list[Ruling]:
    """The same fold over the one reference a ruling carries.

    A ruling names no element of its own — that was given up deliberately when
    the critic stopped re-emitting drafts — except inside a ``needs-info``
    verdict's ``related_unknowns``, which points at the unknown that has to be
    answered. Unresolvable there is not fatal on the first look: it routes to
    the bounded ``recritic``. Snapping it means a re-ask is spent on a critic
    that pointed somewhere real rather than on one that mis-typed a slug.
    """
    return [
        ruling.model_copy(
            update={
                "verdict": ruling.verdict.model_copy(
                    update={
                        "related_unknowns": [
                            # Only the model-reference spelling has an ID to
                            # snap. A subject names no element, so there is
                            # nothing to snap it to.
                            ref.model_copy(
                                update={"element_id": snap(ref.element_id, element_ids)}
                            )
                            if ref.names_an_element
                            else ref
                            for ref in ruling.verdict.related_unknowns
                        ]
                    }
                )
            }
        )
        for ruling in rulings
    ]


class _ReferenceCheck(NamedTuple):
    drafts: list[Claim]
    unresolved: list[UnresolvedReference]
    dropped: list[DroppedClaim]


def _resolve_element_references(
    claims: Iterable[Claim], known_ids: Collection[str]
) -> _ReferenceCheck:
    """Drop every element reference the model does not contain, and mark it.

    The rule every other citation already has. A reference an agent composed
    — well-formed, plausible, absent — costs itself, and the claim stands on
    the elements that resolved. A claim that named elements and lost every one
    is dropped: a finding about nothing is not a finding. A claim that named
    none is a package's own business (ASVS leaves the list empty on a claim
    about the system as a whole) and passes untouched.
    """
    drafts: list[Claim] = []
    unresolved: list[UnresolvedReference] = []
    dropped: list[DroppedClaim] = []
    for claim in claims:
        kept = [ref for ref in claim.affected_element_ids if ref in known_ids]
        lost = [ref for ref in claim.affected_element_ids if ref not in known_ids]
        if not lost:
            drafts.append(claim)
            continue
        if not kept:
            dropped.append(
                DroppedClaim(
                    claim_id=claim.id,
                    title=claim.title,
                    reason=(
                        "names only elements the system model does not contain"
                        f" ({', '.join(repr(ref) for ref in lost)})"
                    )[:DROPPED_REASON_MAX_CHARS],
                )
            )
            continue
        # A blank ID is skipped rather than marked, for the reason
        # :func:`~analysis_service.evidence.resolve_proposals` skips a blank
        # reference: a mark names an element the model does not contain, and an
        # empty string names none. The drop reason above still lists it.
        unresolved += [
            UnresolvedReference(
                claim_id=claim.id, element_id=ref[:ELEMENT_REF_MAX_CHARS]
            )
            for ref in lost
            if ref.strip()
        ]
        drafts.append(claim.model_copy(update={"affected_element_ids": kept}))
    return _ReferenceCheck(drafts, unresolved, dropped)


def _bound_of(
    claim: Claim, known_ids: Collection[str], index: ModelIndex
) -> frozenset[str]:
    """The element IDs a claim may cite, from its grounds or from its prose.

    A catalogued ground names an element or a flow, and the bound is one hop
    from those (:meth:`~analysis_service.system_model.ModelIndex.reach`). A
    claim resting on quotes alone names none, so its bound is exactly what its
    own prose cites — the same resolution
    :func:`mentioned_ids` gives coverage — with no hop, since a description
    that names an element has already put it in reach. A claim citing nothing
    anywhere has no bound and passes untouched.
    """
    places = {ground.place for ground in claim.grounds if ground.place}
    if places:
        return index.reach(places)
    return frozenset(
        resolved
        for mention in mentioned_ids(claim.description)
        if (resolved := canonical(mention, known_ids))
    )


def _bound_element_references(
    claims: Iterable[Claim], index: ModelIndex
) -> _ReferenceCheck:
    """Drop every cited element the claim's own grounds do not reach, and mark it.

    The prompts say reach belongs in the description and
    ``affected_element_ids`` is what the action lands on. This is that rule in
    code (#441): an ID more than one hop from every place the grounds name is
    dropped with :data:`BEYOND_GROUNDS` as its reason, on the same terms as an
    ID the model does not contain, and a claim left with none is dropped.
    """
    known_ids = index.elements.keys()
    drafts: list[Claim] = []
    unresolved: list[UnresolvedReference] = []
    dropped: list[DroppedClaim] = []
    for claim in claims:
        reach = _bound_of(claim, known_ids, index)
        if not reach:
            drafts.append(claim)
            continue
        kept = [ref for ref in claim.affected_element_ids if ref in reach]
        lost = [ref for ref in claim.affected_element_ids if ref not in reach]
        if not lost:
            drafts.append(claim)
            continue
        if not kept:
            dropped.append(
                DroppedClaim(
                    claim_id=claim.id,
                    title=claim.title,
                    reason=(
                        "names only elements its grounds do not reach"
                        f" ({', '.join(repr(ref) for ref in lost)})"
                    )[:DROPPED_REASON_MAX_CHARS],
                )
            )
            continue
        unresolved += [
            UnresolvedReference(
                claim_id=claim.id, element_id=ref, reason=BEYOND_GROUNDS
            )
            for ref in lost
        ]
        drafts.append(claim.model_copy(update={"affected_element_ids": kept}))
    return _ReferenceCheck(drafts, unresolved, dropped)


def _drop_duplicate_ids(
    claims: Iterable[Claim],
) -> tuple[list[Claim], list[DroppedClaim]]:
    """Keep the first draft under each ID; drop and mark every later one.

    An agent that numbered two drafts alike, or filed one requirement twice,
    made a fault in one entry. The first is kept because the lane order is the
    package's own declared order, so the choice is deterministic and the same
    on every run.
    """
    seen: set[str] = set()
    kept: list[Claim] = []
    dropped: list[DroppedClaim] = []
    for claim in claims:
        if claim.id in seen:
            dropped.append(
                DroppedClaim(
                    claim_id=claim.id,
                    title=claim.title,
                    reason="repeats the ID of an earlier draft in this framework",
                )
            )
            continue
        seen.add(claim.id)
        kept.append(claim)
    return kept, dropped


def _attribute_names(element: Element) -> frozenset[str]:
    """The fields an element of this type actually has.

    Attributes are fixed pydantic fields per element class, so "does this
    element have an ``encryption_at_rest``" is mechanical and exact: an
    ``ExternalEntity`` does not, and a reference naming one on it is a
    hallucinated attribute rather than a debatable one.
    """
    return frozenset(type(element).model_fields)


class CriticIssue(NamedTuple):
    """One unresolved-unknown problem, and the claim whose ruling carries it.

    The ID rides beside the message because two callers need different halves.
    The re-ask prompt reads the *message*; the seam that builds that prompt
    reads the *ID*, to decide which drafts the re-ask has to be shown in full.
    Recovering the ID by parsing it back out of the message would make the
    wording of an error string load-bearing for what the graph sends a model.
    """

    claim_id: str
    message: str


class ReviewProblems(NamedTuple):
    """The mechanical check's verdict on one set of rulings.

    ``messages`` is what the re-ask is asked to fix, in the words it reads.
    ``implicated`` is the subset of *drafted* IDs whose drafts the re-ask
    cannot fix without reading: a draft it never ruled, which it must rule
    now, and a draft whose needs-info verdict must be repointed or replaced by
    a verdict the stated facts support. Neither is answerable from an ID.

    Both come out of one pass so they cannot disagree about what went wrong —
    the reason this is a record rather than two functions over the same inputs.
    Falsy when the rulings are assemblable, so callers read it as the check.
    """

    messages: list[str]
    implicated: frozenset[str]
    #: The rulings as :func:`complete_rulings` left them, so assembly reads
    #: the set the check ran over rather than completing it a second time.
    rulings: tuple[Ruling, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.messages)


def _verdict_shape_issues(rulings: Iterable[Ruling]) -> list[CriticIssue]:
    """Every ruling whose verdict's fields disagree with its own ``status``.

    The four rules :class:`~analysis_service.report.Verdict` states, plus the
    one it deliberately does not: a rejection must name the check that killed
    it. That one is asked only here, because a report read back from before the
    field carries no answer and ``None`` is the truthful value for it — see
    :class:`~analysis_service.report.Verdict`. Asked here rather than in the
    schema. The schema is the wrong place for them twice
    over: a provider cannot be made to enforce a dependency between fields, and
    a validator that raises does so at the node boundary, killing the critic
    node — one pass over every draft in the job — with the re-ask that exists
    for exactly this class of problem still unreached.

    So they are returned, like every other problem this module finds, and the
    router sends them to ``recritic``. Each names its claim, because the fix
    is per-ruling: a reason to write, an unknown to name, or a list to drop.

    Deliberately one message per broken rule rather than one per ruling. A
    critic that rejected a claim without a reason *and* attached unknowns to it
    has two independent things to fix, and a merged message would leave the
    second to be discovered on the pass that no longer exists.
    """
    issues = []
    for ruling in rulings:
        verdict = ruling.verdict
        if verdict.status == "needs-info" and not verdict.related_unknowns:
            issues.append(
                CriticIssue(
                    ruling.id,
                    f"claim {ruling.id!r} is ruled needs-info but names no"
                    " unknown attribute in related_unknowns, so nothing says"
                    " what has to be answered",
                )
            )
        if verdict.status != "needs-info" and verdict.related_unknowns:
            issues.append(
                CriticIssue(
                    ruling.id,
                    f"claim {ruling.id!r} is ruled {verdict.status} but carries"
                    " related_unknowns, which is only meaningful on a"
                    " needs-info verdict",
                )
            )
        if verdict.status == "rejected" and verdict.rejected_because is None:
            issues.append(
                CriticIssue(
                    ruling.id,
                    f"claim {ruling.id!r} is rejected but names no check in"
                    " rejected_because, so nothing says which of evidence,"
                    " lane or duplicate ended it",
                )
            )
        if verdict.status != "rejected" and verdict.rejected_because is not None:
            issues.append(
                CriticIssue(
                    ruling.id,
                    f"claim {ruling.id!r} is ruled {verdict.status} but carries"
                    " rejected_because, which is only meaningful on a rejected"
                    " verdict",
                )
            )
        if verdict.status != "confirmed" and not verdict.reason:
            issues.append(
                CriticIssue(
                    ruling.id,
                    f"claim {ruling.id!r} is ruled {verdict.status} and states"
                    " no reason",
                )
            )
    return issues


def _unresolved_unknown_ref_issues(
    rulings: Iterable[Ruling], system_model: SystemModel
) -> list[CriticIssue]:
    """Every ``related_unknowns`` entry naming an element or attribute not there.

    The ``attribute`` half is checked to the same depth as the grounds surface
    below, and no deeper. Requiring the named attribute to actually *hold* the
    ``unknown`` sentinel was refused: it encodes a judgement as a mechanical
    rule and misfires on a stated-but-vague value — "some encryption" in
    ``encryption_at_rest``, where a needs-info is legitimate and the field is
    not literally ``unknown``.

    Depth is matched across the two sites rather than tuned per site, because
    ``Ground``'s unknown-attribute branch and :class:`~analysis_service.report.UnknownRef`
    are spelled identically on purpose — two different mechanical rules over two
    identically-spelled field pairs is a distinction no reader would predict
    from the spelling. What differs is the blast radius, and only that: a
    failure here lands in ``review_issues`` and routes to the bounded
    ``recritic`` re-ask, where a grounds failure kills the job outright.
    """
    by_id = {element.id: element for element in system_model.elements()}
    issues = []
    # Snapped for the check as well as for the report, so the router and
    # assembly agree about which references resolve. Assembly snaps the rulings
    # it actually carries; here a local copy is enough, because nothing in this
    # function's return survives it.
    for ruling in snap_rulings(rulings, by_id.keys()):
        for ref in ruling.verdict.related_unknowns:
            # A question with no place in the model is checked for saying
            # something, and nothing else. There is no model reference to
            # resolve, which is the whole reason the spelling exists.
            if not ref.names_an_element:
                if not ref.subject.strip():
                    issues.append(
                        CriticIssue(
                            ruling.id,
                            f"claim {ruling.id!r} is ruled needs-info and its"
                            " related_unknowns entry names neither an element"
                            " attribute nor a subject, so nothing says what has"
                            " to be answered",
                        )
                    )
                continue
            element = by_id.get(ref.element_id)
            if element is None:
                issues.append(
                    CriticIssue(
                        ruling.id,
                        f"claim {ruling.id!r} hangs its needs-info verdict on"
                        f" element {ref.element_id!r}, which is not in the system"
                        " model",
                    )
                )
            elif ref.attribute not in _attribute_names(element):
                # The available set is named, not just the missing one. An
                # attribute is a fixed field per element *type*, so a critic
                # reaching for `exposure` on an external entity has named a real
                # attribute of the wrong type rather than invented one. Telling
                # the re-ask only what is wrong leaves it guessing; telling it
                # what is there is what lets it repoint rather than fall back on
                # whichever field resolves everywhere.
                issues.append(
                    CriticIssue(
                        ruling.id,
                        f"claim {ruling.id!r} hangs its needs-info verdict on"
                        f" attribute {ref.attribute!r}, which element"
                        f" {ref.element_id!r} does not have. That element has:"
                        f" {', '.join(sorted(_attribute_names(element)))}."
                        " Name one of those, or state the question in"
                        " `subject` if it is not about this model at all",
                    )
                )
    return issues


def _duplicate_id_issues(entries: Iterable[Claim | Ruling]) -> list[str]:
    counts = Counter(entry.id for entry in entries)
    return [
        f"claim ID {claim_id!r} is used by {count} drafts"
        for claim_id, count in counts.items()
        if count > 1
    ]


def _ground_reference_issues(
    claims: Iterable[Claim], system_model: SystemModel, index: ModelIndex
) -> list[str]:
    """Every catalogued grounds entry whose reference does not resolve.

    Set membership, one branch at a time: either attribute branch's
    ``element_id`` and ``attribute`` against the model, a derived-fact's
    ``flow_id`` against the crossings derived from that same model. Every such
    ground is built by the service out of a catalog entry, so a failure here
    is this service's own defect and stays fatal. A quote's ``source_label`` is
    deliberately not here: that is the agent's, and a label naming no source is
    a quote that cannot be found, which :func:`_verify_quotes` marks.

    ``index`` indexes ``system_model``. Both are handed in because the crossings
    are derived from the whole model and an index cannot carry them: the
    derivation fails closed on an invalid model, and an index is built over
    models the validity gate has not seen yet.
    """
    crossing_ids = {crossing.flow_id for crossing in system_model.boundary_crossings()}
    issues = []
    for claim in claims:
        for ground in claim.grounds:
            issues += _one_ground_issues(claim.id, ground, index.elements, crossing_ids)
    return issues


def _one_ground_issues(
    claim_id: str,
    ground: Ground,
    by_id: Mapping[str, Element],
    crossing_ids: Collection[str],
) -> list[str]:
    """The reference failure of one catalogued grounds entry, or nothing."""
    issue = ""
    if ground.kind == "quote":
        return []
    if ground.kind == "absent-element":
        # Nothing to resolve: the term names no element by construction, and
        # that the model names it nowhere was already checked against this same
        # model by ``resolve_proposals``, which dropped it otherwise.
        return []
    if ground.kind == "derived-fact":
        if ground.flow_id not in crossing_ids:
            issue = (
                f"claim {claim_id!r} grounds a derived fact in flow"
                f" {ground.flow_id!r}, which is not a derived boundary crossing"
            )
    else:
        # An unknown attribute and an absent one resolve identically: the check
        # is that the element carries the attribute, never what its value says,
        # for the reason ``related_unknowns`` gives above — a mechanical rule
        # over a value is a judgement in disguise.
        named = (
            "an unknown attribute"
            if ground.kind == "unknown-attribute"
            else "an absent attribute"
        )
        element = by_id.get(ground.element_id)
        if element is None:
            issue = (
                f"claim {claim_id!r} grounds {named} on element"
                f" {ground.element_id!r}, which is not in the system model"
            )
        elif ground.attribute not in _attribute_names(element):
            issue = (
                f"claim {claim_id!r} grounds {named}"
                f" {ground.attribute!r}, which element {ground.element_id!r}"
                " does not have"
            )
    return [issue] if issue else []


class _QuoteCheck(NamedTuple):
    """What the quote check produced: the drafts as they now read, and marks."""

    drafts: list[Claim]
    unverified: list[UnverifiedGround]
    repaired: list[RepairedQuote]
    groundless: list[DroppedClaim]


def _verify_quotes(claims: Sequence[Claim], sources: Mapping[str, str]) -> _QuoteCheck:
    """Check every quote ground against the source it names.

    Three outcomes, at two different scopes, and the split is the whole policy.
    **Per entry**, a quote the ladder refused is offered to
    :func:`~analysis_service.grounding.repair_prepared` first: where the source holds
    a span near enough, the ground is rewritten to that span — the submitter's
    words, never the model's — and a :class:`~analysis_service.report.RepairedQuote`
    keeps what the agent wrote. Otherwise the quote is *marked* and still renders: 0
    failures in 206 measured excerpts is not evidence of zero, and the Rule of
    Three puts the 95% bound at 1.46% per quote — which at the corpus mean of
    18.7 claims per job is a 24% chance that some job dies on a single
    cosmetic mismatch. That is not enough evidence to license killing a job.

    **Per claim**, if *no* ground verifies at all, the claim is dropped and
    recorded as a :class:`~analysis_service.report.DroppedClaim` whose reason
    carries the quotes that were not found. A claim with one bad quote beside
    good ones is still justified; a claim where nothing holds is a finding with
    no machine-checkable justification. Every catalogued ground verifies by set
    membership, so a claim can only lose every ground if every one of them is a
    quote and every quote is bad — which is the common shape of a claim in a
    framework whose catalog rarely holds the fact a requirement turns on, and
    the reason the drop costs the claim rather than the job.

    Returns the drafts as they now read — a repaired ground is a new ground —
    with the groundless ones already removed.
    """
    # Each source folded once rather than once per quote. The ladder normalizes
    # every character of the haystack, and a job runs ~19 claims per framework
    # against the same few submissions, so the per-quote form would re-fold whole
    # documents to reach the same answer.
    folded = {label: normalize(text) for label, text in sources.items()}
    # And each source split and folded per word once, for the repair rung, which
    # needs the source in a second shape. Filled as a source is first repaired
    # against rather than up front: a body whose quotes all verify repairs
    # nothing, and preparing every submission for it would be the whole cost of
    # the rung with none of its work.
    #
    # What it holds for the length of the body is two tuples of words per source
    # a refused quote named, and that is the retention reuse costs: a fold
    # nobody keeps is a fold the next quote pays for again.
    #
    # Measured at 18x the source bytes, flat from 1,000 words to 50,000 -- one
    # Python ``str`` per word, twice, is almost all of it, and
    # ``evals/bench/deterministic.py retention`` re-derives it. The shipped
    # ``resilience.max_source_bytes`` is 102,400 bytes for a whole job, so a
    # body holds at most about 1.8 MiB and the eight-slot node pool about 14
    # MiB. It is bounded by that cap rather than by anything here, which is why
    # the cap is the thing to read before raising it.
    prepared: dict[str, PreparedSource] = {}
    # One deadline for every repair this body runs. Each scan is bounded on its
    # own, and a body runs one scan per refused quote: bounded per scan, a body
    # of four hundred adversarial quotes ran for hours after the job settled.
    deadline = repair_deadline()
    drafts: list[Claim] = []
    marks: list[UnverifiedGround] = []
    repaired: list[RepairedQuote] = []
    groundless: list[DroppedClaim] = []
    for claim in claims:
        grounds = list(claim.grounds)
        unverified: list[int] = []
        repairs: list[RepairedQuote] = []
        for index, ground in enumerate(grounds):
            if ground.kind != "quote":
                continue
            if verify_normalized(ground.text, folded.get(ground.source_label, "")):
                continue
            if ground.source_label not in sources:
                unverified.append(index)
                continue
            label = ground.source_label
            if label not in prepared:
                # The body's deadline, asked before the fold rather than inside
                # the scan after it. A body that has spent its thirty seconds
                # answers ``None`` for every quote that is left, and folding a
                # whole submission to reach that answer is what this ordering
                # removes: 400 refused quotes against a 20,000-word source is
                # 6.4 s of folding for 400 answers of nothing.
                if deadline_spent(deadline):
                    unverified.append(index)
                    continue
                prepared[label] = prepare_source(sources[label])
            repair = repair_prepared(ground.text, prepared[label], deadline)
            if repair is None:
                unverified.append(index)
                continue
            span, similarity = repair
            grounds[index] = ground.model_copy(update={"text": span})
            repairs.append(
                RepairedQuote(
                    claim_id=claim.id,
                    index=index,
                    written=ground.text,
                    similarity=round(similarity, 3),
                )
            )
        if len(unverified) == len(grounds):
            lost = "; ".join(
                f"{grounds[index].text!r} not found in {grounds[index].source_label!r}"
                for index in unverified
            )
            groundless.append(
                DroppedClaim(
                    claim_id=claim.id,
                    title=claim.title,
                    reason=f"no ground verifies: {lost}"[:DROPPED_REASON_MAX_CHARS],
                )
            )
            continue
        drafts.append(
            claim.model_copy(update={"grounds": grounds}) if repairs else claim
        )
        repaired += repairs
        marks += [
            UnverifiedGround(
                claim_id=claim.id,
                index=index,
                reason=(
                    f"not found in {grounds[index].source_label!r}"
                    if grounds[index].source_label in sources
                    else f"names source {grounds[index].source_label!r}, which is"
                    " not one of this job's sources"
                ),
            )
            for index in unverified
        ]
    return _QuoteCheck(drafts, marks, repaired, groundless)


def join_drafts(
    drafts_by_lane: Mapping[str, Sequence[Claim]],
    package: FrameworkPackage,
    system_model: SystemModel,
    sources: Mapping[str, str] = MappingProxyType({}),
) -> JoinedDrafts:
    """Merge one framework's lane agents' drafts into the list its critic sees.

    **One package per call.** Two frameworks' drafts never meet here: they are
    ruled by different critics against different questions, and merging them
    would put a duplicate check across claims that cannot duplicate each other.
    The graph runs this once per selected framework.

    The package's own declared lane order, so a critic reads the lanes in the
    same order every run. ID prefixes and each ``Ground``'s own shape are
    enforced by construction — :func:`~analysis_service.evidence.resolve_proposals`
    composes both — and what this seam adds is the checks that need the whole
    set: element references resolving against the System Model, IDs unique
    across it, and every grounds entry resolving and, for a quote, actually
    appearing in the source it names.

    **Whether a draft sits in the right lane is not among them, and cannot be.**
    A draft's lane is stamped from the node by
    :func:`~analysis_service.evidence.resolve_proposals` rather than written by
    the agent, so comparing the two would compare a value against the value it
    was copied from. The question that survives is whether the claim *belongs*
    in the lane it was found in, which is about the finding's content rather
    than its serialization, and is a critic's judgement step.

    Two things it *marks* rather than fails on, because the fan-in has no
    re-ask path and a whole report is too much to trade for either: a quote
    absent from the source it names, and an element ID a description cites in
    prose that the model does not contain
    (:class:`~analysis_service.report.UnresolvedMention`). A claim whose every
    ground is such a quote is *dropped* and marked
    (:class:`~analysis_service.report.DroppedClaim`), on the same trade. A
    package's record adds whatever else its own judgement fields earn
    (:meth:`~analysis_service.report.Claim.claim_marks`).

    References are snapped to their canonical spelling first
    (:func:`snap_drafts`), so the checks below — and the report, which carries
    these drafts' own fields through unaltered — see the spelling the job holds
    rather than each agent's. That is recognition and never resolution: a
    reference naming nothing is left exactly as written, for the check to report
    in the agent's own words.

    The fan-in is where this belongs because it is the first point at which all
    of this framework's lanes' drafts, the System Model and the job's sources
    exist together.

    ``sources`` maps each source's label to its text. It defaults to empty for
    the same reason the validity gate's citation rule takes its label set as a
    parameter: a hand-authored model driven through the in-process engine has
    no sources to check against, and inventing a set would fail it on a
    citation that is not wrong. Empty means the text check does not run — no
    quote is marked and no claim is dropped on one.
    """
    # One index for the whole fan-in. Each check below asks the model who
    # carries an ID and what a flow runs between, once per claim and once per
    # grounds entry within it, and each used to walk the model to answer.
    index = ModelIndex.of(system_model)
    known_ids = index.elements.keys()
    snapped = snap_drafts(
        [draft for lane in package.lanes for draft in drafts_by_lane.get(lane, ())],
        known_ids,
        sources.keys(),
    )
    unique, duplicates = _drop_duplicate_ids(snapped)
    referenced = _resolve_element_references(unique, known_ids)
    issues = _ground_reference_issues(referenced.drafts, system_model, index)
    if issues:
        raise DraftJoinError("; ".join(issues))
    bounded = _bound_element_references(referenced.drafts, index)
    checked = (
        _verify_quotes(bounded.drafts, sources)
        if sources
        else _QuoteCheck(list(bounded.drafts), [], [], [])
    )
    kept = checked.drafts
    return JoinedDrafts(
        drafts=kept,
        marks=AnalysisMarks(
            unverified_grounds=checked.unverified,
            repaired_quotes=checked.repaired,
            unresolved_references=[*referenced.unresolved, *bounded.unresolved],
            unresolved_mentions=_unresolved_mentions(kept, known_ids),
            dropped_claims=[
                *duplicates,
                *referenced.dropped,
                *bounded.dropped,
                *checked.groundless,
            ],
        ).merged_with(package.record.claim_marks(kept)),
    )


def complete_rulings(
    drafts: Sequence[Claim], rulings: Sequence[Ruling]
) -> list[Ruling]:
    """Each ruling with what the draft's grounds settle already filled in.

    A draft whose grounds settle it (:meth:`Claim.settled_by_grounds`) is not
    shown to the critic, and where the critic wrote no ruling on it the
    settled ruling is added here. Where a critic did rule on one — a scripted
    critic reads every draft — a ``needs-info`` ruling gains the pairs in
    ``related_unknowns`` (beside any the critic named) and, where the critic
    wrote none, a reason naming them. What a critic may not do is confirm such
    a draft, which :func:`review_issues` reports.

    A ruling on a draft the package's own table calls misfiled
    (:meth:`~analysis_service.report.Claim.misfiled`) becomes ``rejected`` with
    the table's reason and ``rejected_because="lane"``, whatever the critic
    ruled. That is the one rejection this service writes rather than reads.

    Rulings on drafts with no such ground pass through untouched, and so does a
    ruling that names no drafted ID: the reconciliation check owns that.
    """
    by_id = {draft.id: draft.unknown_grounds() for draft in drafts}
    misfiled = {
        draft.id: reason for draft in drafts if (reason := type(draft).misfiled(draft))
    }
    ruled_ids = {ruling.id for ruling in rulings}
    settled = [
        ruling
        for draft in drafts
        if draft.id not in ruled_ids
        and (ruling := type(draft).settled_by_grounds(draft)) is not None
    ]
    completed = []
    for ruling in (*rulings, *settled):
        if ruling.id in misfiled:
            # A lane error is a table lookup, so the ruling is the table's
            # whatever the critic said (#442). The reason names the lanes the
            # verb belongs to, which is what the audit array owes a reader, and
            # the step is `lane` by construction: the table is the lane check.
            verdict = ruling.verdict.model_copy(
                update={
                    "status": "rejected",
                    "reason": misfiled[ruling.id],
                    "related_unknowns": [],
                    "rejected_because": "lane",
                }
            )
            completed.append(ruling.model_copy(update={"verdict": verdict}))
            continue
        derived = by_id.get(ruling.id, [])
        if ruling.verdict.status != "needs-info" or not derived:
            completed.append(ruling)
            continue
        named = {
            (ref.element_id, ref.attribute) for ref in ruling.verdict.related_unknowns
        }
        added = [ref for ref in derived if (ref.element_id, ref.attribute) not in named]
        reason = ruling.verdict.reason or (
            f"The claim rests on {_named(derived)}, which the input never stated."
        )
        verdict = ruling.verdict.model_copy(
            update={
                "related_unknowns": [*ruling.verdict.related_unknowns, *added],
                "reason": reason,
            }
        )
        completed.append(ruling.model_copy(update={"verdict": verdict}))
    return completed


def _named(unknowns: Sequence[UnknownRef]) -> str:
    return ", ".join(f"`{ref.attribute}` on `{ref.element_id}`" for ref in unknowns)


def unsettled_drafts(drafts: Sequence[Claim]) -> list[Claim]:
    """The drafts a critic reads: every one its own grounds do not settle.

    A draft :meth:`Claim.settled_by_grounds` rules is ruled in code and never
    shown, so the critic spends nothing on it (#439).
    """
    return [draft for draft in drafts if type(draft).settled_by_grounds(draft) is None]


def _confirmed_on_unknown_issues(
    drafts: Sequence[Claim], rulings: Iterable[Ruling]
) -> list[CriticIssue]:
    """Every ``confirmed`` ruling on a draft whose grounds cite an unknown.

    The draft's own evidence says the fact is open, so a confirmation asserts
    what the model does not state. The critic's choices on such a draft are
    ``needs-info`` — which the service completes — or ``rejected`` with a
    reason, and a re-ask is what turns a confirmation into one of those.
    """
    by_id = {draft.id: draft.unknown_grounds() for draft in drafts}
    return [
        CriticIssue(
            ruling.id,
            f"claim {ruling.id!r} is ruled confirmed but its own grounds cite"
            f" {_named(by_id[ruling.id])} as never stated, so it cannot"
            " be confirmed: rule it needs-info, or reject it with a reason",
        )
        for ruling in rulings
        if ruling.verdict.status == "confirmed" and by_id.get(ruling.id)
    ]


def endpoint_targets(
    element_ids: Iterable[str], flows: Mapping[str, tuple[str, str]]
) -> frozenset[str]:
    """The cited elements with every flow replaced by its two endpoints.

    One place in the graph, spelled one way: a claim citing a flow and one
    citing the process at its end name the same place. Trust boundaries are
    dropped, since a zone is the context a claim sits in rather than what it is
    about. The same fold ``evals/harness/identity.py`` applies when it scores,
    kept in step by ``tests/test_evals_identity.py``.

    ``flows`` is each flow's ID against its two endpoints — a
    :attr:`~analysis_service.system_model.ModelIndex.flow_endpoints` — rather
    than the model, and the eval side's ``endpoint_form`` takes the same map.
    A caller folding many claims builds it once: derived per call, the fold
    walked every flow in the model for every claim it was asked about.
    """
    targets: set[str] = set()
    for element_id in element_ids:
        if element_id.startswith(f"{TrustBoundary.id_prefix}:"):
            continue
        endpoints = flows.get(element_id)
        if endpoints is None:
            targets.add(element_id)
        else:
            targets.update(endpoints)
    return frozenset(targets)


def duplicate_groups(
    drafts: Sequence[Claim], system_model: SystemModel
) -> dict[str, list[str]]:
    """Each draft's ID against the other drafts naming one action at one place.

    The critic's duplicate step is a comparison of two fields — the verb and
    the endpoint-resolved targets — made here so the critic reads the pairs
    rather than hunting for them across every lane (#440). Lanes are not compared: a read and a write of one
    flow carry two verbs, and two lanes filing one verb at one place is the
    duplicate the step exists to catch.

    A draft with no verb belongs to a package whose identity is a catalog
    identifier, and its duplicates are ID collisions the join already refuses.
    """
    flows = ModelIndex.of(system_model).flow_endpoints
    by_key: dict[tuple[str, frozenset[str]], list[str]] = {}
    for draft in drafts:
        if draft.verb is None:
            continue
        key = (draft.verb, endpoint_targets(draft.affected_element_ids, flows))
        by_key.setdefault(key, []).append(draft.id)
    return {
        draft_id: [other for other in ids if other != draft_id]
        for ids in by_key.values()
        if len(ids) > 1
        for draft_id in ids
    }


def rating_disagreements(drafts: Sequence[Claim]) -> dict[str, list[str]]:
    """Each draft against the others with one fact pattern and another rating.

    Critic step 4 asks that identical fact patterns carry identical ratings
    across lanes. Two drafts with the same verb and the same set of catalogued
    grounds are one fact pattern, and whether their ``likelihood`` or
    ``impact`` differ is a comparison of four fields, made here so the critic
    reads the pair and only picks the rating (#444). Quotes are left out of the
    key: two agents quoting two spans of one sentence are not two patterns.

    The ratings are read through :meth:`Claim.rating_of`, so a framework that
    grades nothing answers ``None`` and is never compared.
    """
    by_pattern: dict[tuple[str, frozenset[tuple[str, str, str]]], list[Claim]] = {}
    ratings: dict[str, tuple[str, str]] = {}
    for draft in drafts:
        rating = type(draft).rating_of(draft)
        if draft.verb is None or rating is None:
            continue
        ratings[draft.id] = rating
        facts = frozenset(
            (ground.kind, ground.place or ground.term, ground.attribute)
            for ground in draft.grounds
            if ground.kind != "quote"
        )
        if facts:
            by_pattern.setdefault((draft.verb, facts), []).append(draft)
    disagreements: dict[str, list[str]] = {}
    for group in by_pattern.values():
        if len({ratings[d.id] for d in group}) < 2:
            continue
        for draft in group:
            disagreements[draft.id] = [d.id for d in group if d.id != draft.id]
    return disagreements


def review_issues(
    drafts: Sequence[Claim],
    rulings: Sequence[Ruling],
    system_model: SystemModel,
) -> ReviewProblems:
    """Every way the critic's rulings fail to account for the drafts it saw.

    The mechanical check, returned rather than raised, so the graph can *route*
    on it: a falsy result means the rulings are assemblable, a truthy one is
    what the bounded re-ask is asked to fix. The critic must rule on exactly the
    drafted set — no claim invented, none dropped — with unique IDs, with each
    verdict carrying the fields its own ``status`` calls for, and each
    ``needs-info`` naming only unknowns the model actually contains.

    **Verdict shape is checked here rather than by the schema**, and that is
    the reason this function is worth reading twice. The rules are conditional
    on ``status``, which no provider schema can express, so they can only be
    enforced after the fact — and enforcing them in a pydantic validator means
    enforcing them at the node boundary, where a raise kills the critic node
    and the whole job with it. Every other problem in this list gets a bounded
    re-ask; a missing reason is not a worse fault than a dropped draft, and
    there is no reason for it to be the fatal one.

    **A malformed claim ID is checked here too, and by accident rather than by
    design.** Nothing below looks at an ID's spelling — which is what lets this
    run over a framework whose IDs are requirement numbers: the set comparison at
    the top requires the ruled IDs to equal the drafted ones, which an ill-formed
    ID fails on both sides at once — the draft it meant to name reads as dropped,
    and the ID it actually wrote reads as invented. That is a stronger
    constraint than a pattern and it produces better messages, so
    :class:`~analysis_service.report.Ruling` carries no pattern to fire first and
    fatally.

    Element references are deliberately **not** checked: a ruling carries none.
    They are the join seam's business (:func:`join_drafts` fails closed on a
    draft citing an element the model does not contain), and since the critic
    no longer re-emits them there is no second place they can break. An issue
    listed here has to be one the re-ask can actually fix, and a draft's bad
    reference never was.
    """
    rulings = complete_rulings(drafts, rulings)
    drafted_ids = {draft.id for draft in drafts}
    ruled_ids = {ruling.id for ruling in rulings}
    dropped = sorted(drafted_ids - ruled_ids)
    per_ruling = (
        _confirmed_on_unknown_issues(drafts, rulings)
        + _verdict_shape_issues(rulings)
        + _unresolved_unknown_ref_issues(rulings, system_model)
    )
    messages = [f"critic dropped draft {claim_id!r}" for claim_id in dropped]
    messages += [
        f"critic returned claim {claim_id!r}, which no lane agent drafted"
        for claim_id in sorted(ruled_ids - drafted_ids)
    ]
    messages += _duplicate_id_issues(rulings)
    messages += [issue.message for issue in per_ruling]
    # A duplicate ID implicates no draft: the re-ask drops one of two rulings on
    # an ID it already ruled, which is answerable from the rulings alone. An
    # invented ID implicates none either — there is no draft behind it to show.
    #
    # Every per-ruling problem does implicate one. Naming the unknown a
    # needs-info hangs on, or writing the reason a rejection owes a reader,
    # cannot be done from an ID — both are assertions about a specific claim,
    # and a re-ask that cannot read it would have to invent one.
    implicated = set(dropped) | {
        issue.claim_id for issue in per_ruling if issue.claim_id in drafted_ids
    }
    return ReviewProblems(
        messages=messages, implicated=frozenset(implicated), rulings=tuple(rulings)
    )


def _ruled(draft: Claim, ruling: Ruling, ruled_record: type[RuledClaim]) -> RuledClaim:
    """One draft plus the critic's ruling on it, as the package's ruled record.

    The draft's own fields are carried across from the copy the service already
    held rather than from anything the critic emitted, so a review cannot alter
    a description or an element reference.

    **What a ruling may replace is stated by the ruling's own shape, not by a
    list here.** Every field a package's :class:`~analysis_service.report.Ruling`
    subclass declares beyond ``id`` and ``verdict`` is merged onto the draft, and
    a field holding ``None`` leaves the draft's alone. That one rule covers both
    of the things a package actually does with those fields: STRIDE's
    ``confidence`` is a judgement the draft never had and is required, so it
    always lands; STRIDE's ``severity`` is a draft field the calibration step may
    replace, and its ``None`` — the common case — keeps the agent's rating and the
    justification that argues for it together.

    The verdict is **rebuilt** rather than carried across, promoting the
    critic's unruled :class:`~analysis_service.report.ProposedVerdict` to the
    :class:`~analysis_service.report.Verdict` the report defines. It cannot fail:
    :func:`review_issues` has already passed on exactly these rulings, and its
    three verdict checks are that model's validator asked one seam earlier. A
    raise here would mean the two had drifted, which is why the promotion is
    left able to raise rather than coerced.
    """
    overrides = {
        field: value
        for field, value in ruling.model_dump(exclude={"id", "verdict"}).items()
        if value is not None
    }
    return ruled_record(
        **{**draft.model_dump(), **overrides},
        verdict=Verdict.model_validate(ruling.verdict.model_dump()),
    )


def assemble_claims(
    drafts: Sequence[Claim],
    rulings: Sequence[Ruling],
    system_model: SystemModel,
    schemas: FrameworkSchemas,
) -> AssembledClaims:
    """Merge one critic's rulings onto its drafts, split into the block's arrays.

    :func:`review_issues` is the gate — one definition of what "well-formed
    critic output" means, shared with the router that decides whether to
    re-ask. Assembly runs only after that gate has passed, but re-checks here
    and fails closed regardless: nothing reaches the report on output that did
    not survive the check. Rejected claims ride in their own audit array; the
    rest are ordered by :func:`_claim_order`.

    Claims are built in ``drafts`` order — the package's own lane order, as
    :func:`join_drafts` left them — so the audit array does not inherit
    whatever order the critic happened to emit its rulings in.
    """
    problems = review_issues(drafts, rulings, system_model)
    if problems:
        raise CriticOutputError("; ".join(problems.messages))

    rulings = snap_rulings(
        problems.rulings, {element.id for element in system_model.elements()}
    )
    ruling_by_id = {ruling.id: ruling for ruling in rulings}
    reviewed = [
        _ruled(draft, ruling_by_id[draft.id], schemas.ruled_record) for draft in drafts
    ]
    actionable = [claim for claim in reviewed if claim.verdict.status != "rejected"]
    rejected = [claim for claim in reviewed if claim.verdict.status == "rejected"]
    return AssembledClaims(sorted(actionable, key=_claim_order), rejected)


def _claim_order(claim: RuledClaim) -> tuple[int, str]:
    """Most severe first where the framework grades harm, then by ID.

    ``severity`` is read off the record rather than declared, the same way
    :meth:`~analysis_service.frameworks.FrameworkPackage.carries_severity` reads
    it: a framework that grades nothing has every claim on one rank and falls
    through to the ID, which is a stable order rather than the critic's emission
    order.
    """
    severity = getattr(claim, "severity", None)
    rank = SEVERITY_ORDER.index(severity.level) if severity is not None else 0
    return rank, claim.id


_DRAFT_UNRULED_FIELDS = frozenset({"mitigations"})


def _ruling_view(
    drafts: Sequence[Claim],
    duplicates: Mapping[str, Sequence[str]] = MappingProxyType({}),
    rated_unlike: Mapping[str, Sequence[str]] = MappingProxyType({}),
) -> list[dict]:
    """The drafts as a critic reads them: no recommendations, no empty branches.

    A critic's steps read ``description`` (evidence), the lane,
    ``affected_element_ids`` (duplicate), and ``grounds`` — plus whatever its own
    framework grades. ``mitigations`` is read by none of them, and the prompt
    already says so. A :class:`~analysis_service.report.Mitigation` is a
    200-character summary plus 2000 characters of detail, and a draft carries a
    list of them, so this is the largest block in the longest prompt the graph
    sends that no judgement is spent on. Same argument as
    :func:`~analysis_service.graph._without_source_fields`, one node further down.

    ``exclude_defaults`` is what drops the empty branches of a
    :class:`~analysis_service.report.Ground`. That model is one flat object
    rather than a discriminated union — a deliberate choice, for provider
    schema-compiler reasons it documents itself — so four of its six fields are
    the empty string on any given ground, and rendering them spends a line each
    on a field whose own validator forbids it carrying anything.

    THE HAZARD THIS BUYS, stated rather than left to be discovered: a field
    added to a package's record with a default now disappears from that
    framework's critic's view whenever it holds that default, silently and with
    nothing downstream able to see it. Every field a critic rules on is required
    today and so cannot be dropped;
    ``test_ruling_view_keeps_every_field_the_critic_rules_on`` is what holds that
    true for the next field.

    ``framework`` and ``framework_version`` go too, under the same rule: they are
    the same pair on every draft in one critic's prompt — it rules one
    framework's drafts — so they are a constant repeated per claim.
    """
    views = []
    for draft in drafts:
        view = draft.model_dump(
            mode="json",
            exclude={*_DRAFT_UNRULED_FIELDS, "framework", "framework_version"},
            exclude_defaults=True,
        )
        # Computed, never drafted: the IDs of the other drafts naming the same
        # action at the same place (:func:`~analysis_service.critic.duplicate_groups`),
        # so the critic's duplicate step reads a pair instead of hunting for it.
        if draft.id in duplicates:
            view["same_action_as"] = list(duplicates[draft.id])
        # Also computed: the package's own table says this draft's action is not
        # one its lane files. The ruling is settled in code; the key tells the
        # critic not to spend a judgement on it.
        if reason := type(draft).misfiled(draft):
            view["filed_in_wrong_lane"] = reason
        # Computed too: the other drafts with this one's fact pattern and a
        # different rating (:func:`~analysis_service.critic.rating_disagreements`),
        # which is the pair step 4 calibrates across.
        if draft.id in rated_unlike:
            view["rated_unlike"] = list(rated_unlike[draft.id])
        views.append(view)
    return views


def critic_view(
    drafts: Sequence[Claim],
    system_model: SystemModel,
    *,
    only: Collection[str] | None = None,
) -> list[dict]:
    """The drafts a critic is shown, with everything computed for it already.

    One function rather than four calls in the right order, because the graph
    builds this view twice — once for the first pass over the whole fan-in, once
    for the bounded re-ask over the few drafts it names — and the two must agree
    about what a critic reads. A draft its own grounds settle is dropped first
    (:func:`unsettled_drafts`), then the pairs the critic would otherwise hunt
    for are computed and attached.

    **The pairs are computed over every shown draft, never over ``only``.** A
    duplicate is a relation between two drafts, so narrowing the set first would
    leave a draft paired with nothing and read as unique. ``only`` narrows what
    is *rendered* and nothing else: the re-ask reproduces rulings rather than
    drafts, and an ID is the whole of a claim it need not read.
    """
    shown = unsettled_drafts(drafts)
    duplicates = duplicate_groups(shown, system_model)
    rated_unlike = rating_disagreements(shown)
    chosen = shown if only is None else [d for d in shown if d.id in only]
    return _ruling_view(chosen, duplicates, rated_unlike)


@dataclass(frozen=True)
class Accepted:
    """One critic pass that reconciled with its drafts."""

    #: How many rulings it returned, for the routing event.
    count: int


@dataclass(frozen=True)
class Revision:
    """One critic pass that did not, and everything the re-ask needs.

    Built in one place so **the prompt and the check cannot disagree about
    which claims are in trouble**. The messages say what did not reconcile and
    the view carries the drafts those messages name, and both come out of the
    same call over the same set.
    """

    #: One message per problem, as the re-ask is asked to fix them.
    messages: list[str]
    #: Every drafted ID: the covering set the re-ask must reproduce.
    roster: list[str]
    #: The few drafts a structural fix cannot be made without reading.
    unreconciled: list[dict]


def review(
    drafts: Sequence[Claim], rulings: Sequence[Ruling], system_model: SystemModel
) -> Accepted | Revision:
    """Rule on one critic pass: reconciled, or a revision and what it must read.

    The whole mechanical check on a critic's output, and the whole of what a
    re-ask is told, behind one call. A caller routes on which of the two it
    gets back and parks what that value carries; deciding *what* a re-ask reads
    is this module's, because it is the same judgement as deciding what the
    first pass read.

    The re-ask sees a **roster of IDs plus the few it must read**, not the whole
    set again. Its job is structural — cover exactly the drafted IDs, once each,
    with unknowns that resolve — and an ID carries the whole of that claim.
    """
    problems = review_issues(drafts, rulings, system_model)
    if not problems:
        return Accepted(count=len(rulings))
    shown = unsettled_drafts(drafts)
    return Revision(
        messages=list(problems.messages),
        roster=[draft.id for draft in shown],
        unreconciled=critic_view(drafts, system_model, only=problems.implicated),
    )
