"""Mechanical checks at the join and assemble seams around a framework's critic.

The deterministic half of the critic step: mechanical checks belong in code,
prompts carry only judgement. Everything here is a check no model should be
asked to perform — that a framework's lane agents' drafts cite elements the
System Model actually contains, that claim IDs are unique, that every grounds
entry resolves and every quote ground is really in the source it names, and that
the critic ruled on exactly the drafts it was given, each ruling carrying a
well-formed verdict. A package's critic prompt names these as already done so its
judgement is spent on evidence, lanes, duplicates and whatever else that
framework grades — and, for grounds, on the one question code cannot answer:
whether a quote that is verbatim actually *supports* the finding it was filed
under.

**Neutral, and one seam per framework rather than one across frameworks.** Every
check here reads :class:`~stride_service.report.Claim`,
:class:`~stride_service.report.Ruling` and the package contract, so a second
framework's output goes through the same code. What it never does is merge two
frameworks' drafts: the join runs per package, in that package's own declared
lane order, because two frameworks' claims are not comparable and a duplicate
across them is not a duplicate.

The assemble seam is also where a ruling becomes a claim. A critic emits
judgements keyed by draft ID rather than the drafts themselves
(:class:`~stride_service.report.Ruling`), so the agent's own fields reach the
report from the copy this service already holds — not round-tripped through a
model that was never asked to change them.

Model output is untrusted input (OWASP LLM05): it is validated here, before
anything reaches the report. Both seams fail closed with every issue listed
at once — an agent that hallucinates an element ID or a critic that drops
claims is a defect to surface loudly, never to paper over by discarding the
offending entries.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import NamedTuple, get_args

from stride_service.frameworks import FrameworkPackage, FrameworkSchemas
from stride_service.grounding import normalize, verify_normalized
from stride_service.references import canonical, snap
from stride_service.report import (
    GROUNDLESS_REASON_MAX_CHARS,
    AnalysisMarks,
    Claim,
    Ground,
    GroundlessClaim,
    RuledClaim,
    Ruling,
    SeverityLevel,
    UnresolvedMention,
    UnverifiedGround,
    Verdict,
)
from stride_service.system_model import DataFlow, Element, SystemModel

# Most severe first — the order a graded framework's ``claims`` array carries.
# The service holds the order because it holds
# :data:`~stride_service.report.SeverityLevel`; whether a framework grades at all
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
    are the agents'; the :class:`~stride_service.report.AnalysisMarks` are the
    *service's* record of what each draft failed to make good on — a quote that
    is not in the source it named, an element ID a description cited that does
    not exist, and whatever the package's own record adds
    (:meth:`~stride_service.report.Claim.claim_marks`). They ride beside the
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
        UnresolvedMention(claim_id=claim.id, mention=mention)
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
    return ground.model_copy(update={"flow_id": snap(ground.flow_id, element_ids)})


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
                            ref.model_copy(
                                update={"element_id": snap(ref.element_id, element_ids)}
                            )
                            for ref in ruling.verdict.related_unknowns
                        ]
                    }
                )
            }
        )
        for ruling in rulings
    ]


def _unresolved_reference_issues(
    claims: Iterable[Claim], system_model: SystemModel
) -> list[str]:
    known_ids = {element.id for element in system_model.elements()}
    return [
        f"claim {claim.id!r} references element {ref!r}, which is not in the"
        " system model"
        for claim in claims
        for ref in claim.affected_element_ids
        if ref not in known_ids
    ]


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

    def __bool__(self) -> bool:
        return bool(self.messages)


def _verdict_shape_issues(rulings: Iterable[Ruling]) -> list[CriticIssue]:
    """Every ruling whose verdict's fields disagree with its own ``status``.

    The three rules :class:`~stride_service.report.Verdict` states, asked here
    rather than in the schema. The schema is the wrong place for them twice
    over: a provider cannot be made to enforce a dependency between fields, and
    a validator that raises does so at the node boundary, killing the critic
    node — one pass over every draft in the job — with the re-ask that exists
    for exactly this class of problem still unreached.

    So they are returned, like every other problem this module finds, and the
    router sends them to ``recritic``. Each names its claim, because the fix
    is per-ruling: a reason to write, an unknown to name, or a list to drop.

    Deliberately three separate messages rather than one per ruling. A critic
    that rejected a claim without a reason *and* attached unknowns to it has
    two independent things to fix, and a merged message would leave the second
    to be discovered on the pass that no longer exists.
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
    ``Ground``'s unknown-attribute branch and :class:`~stride_service.report.UnknownRef`
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
                issues.append(
                    CriticIssue(
                        ruling.id,
                        f"claim {ruling.id!r} hangs its needs-info verdict on"
                        f" attribute {ref.attribute!r}, which element"
                        f" {ref.element_id!r} does not have",
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
    claims: Iterable[Claim],
    system_model: SystemModel,
    source_labels: Collection[str],
) -> list[str]:
    """Every grounds entry whose reference does not resolve.

    Set membership, one branch at a time: a quote's ``source_label`` against
    the job's labels, either attribute branch's ``element_id`` and
    ``attribute`` against the model, a derived-fact's ``flow_id`` against the
    crossings derived from that same model. The gate's own stated principle is
    what puts it here — *set membership is mechanical, so it belongs in code
    rather than in a prompt* — and it inherits that rule's escape: where a job
    supplies no labels, the label half does not run, so a hand-authored model
    driven through the in-process engine is not failed on a citation that is
    not wrong.
    """
    by_id = {element.id: element for element in system_model.elements()}
    crossing_ids = {crossing.flow_id for crossing in system_model.boundary_crossings()}
    legal_labels = frozenset(source_labels)
    issues = []
    for claim in claims:
        for ground in claim.grounds:
            issues += _one_ground_issues(
                claim.id, ground, by_id, crossing_ids, legal_labels
            )
    return issues


def _one_ground_issues(
    claim_id: str,
    ground: Ground,
    by_id: Mapping[str, Element],
    crossing_ids: Collection[str],
    legal_labels: Collection[str],
) -> list[str]:
    """The reference failure of one grounds entry, by branch, or nothing."""
    issue = ""
    if ground.kind == "quote":
        if legal_labels and ground.source_label not in legal_labels:
            issue = (
                f"claim {claim_id!r} grounds a quote in source"
                f" {ground.source_label!r}, which is not one of this job's"
                f" sources {sorted(legal_labels)}"
            )
    elif ground.kind == "derived-fact":
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


def _verify_quotes(
    claims: Sequence[Claim], sources: Mapping[str, str]
) -> tuple[list[UnverifiedGround], list[GroundlessClaim]]:
    """Check every quote ground against the source it names.

    Two outcomes, at two different scopes, and the split is the whole policy.
    **Per entry**, an unverifiable quote is *marked* and still renders: 0
    failures in 206 measured excerpts is not evidence of zero, and the Rule of
    Three puts the 95% bound at 1.46% per quote — which at the corpus mean of
    18.7 claims per job is a 24% chance that some job dies on a single
    cosmetic mismatch. That is not enough evidence to license killing a job.

    **Per claim**, if *no* ground verifies at all, the claim is dropped and
    recorded as a :class:`~stride_service.report.GroundlessClaim` whose reason
    carries the quotes that were not found. A claim with one bad quote beside
    good ones is still justified; a claim where nothing holds is a finding with
    no machine-checkable justification. Every catalogued ground verifies by set
    membership, so a claim can only lose every ground if every one of them is a
    quote and every quote is bad — which is the common shape of a claim in a
    framework whose catalog rarely holds the fact a requirement turns on, and
    the reason the drop costs the claim rather than the job.

    Nothing is filtered here: the caller removes the groundless claims.
    """
    # Each source folded once rather than once per quote. The ladder normalizes
    # every character of the haystack, and a job runs ~19 claims per framework
    # against the same few submissions, so the per-quote form would re-fold whole
    # documents to reach the same answer.
    folded = {label: normalize(text) for label, text in sources.items()}
    marks: list[UnverifiedGround] = []
    groundless: list[GroundlessClaim] = []
    for claim in claims:
        unverified = [
            index
            for index, ground in enumerate(claim.grounds)
            if ground.kind == "quote"
            and not verify_normalized(ground.text, folded.get(ground.source_label, ""))
        ]
        if len(unverified) == len(claim.grounds):
            lost = "; ".join(
                f"{claim.grounds[index].text!r} not found in"
                f" {claim.grounds[index].source_label!r}"
                for index in unverified
            )
            groundless.append(
                GroundlessClaim(
                    claim_id=claim.id,
                    title=claim.title,
                    reason=f"no ground verifies: {lost}"[:GROUNDLESS_REASON_MAX_CHARS],
                )
            )
            continue
        marks += [
            UnverifiedGround(
                claim_id=claim.id,
                index=index,
                reason=f"not found in {claim.grounds[index].source_label!r}",
            )
            for index in unverified
        ]
    return marks, groundless


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
    enforced by construction — :func:`~stride_service.evidence.resolve_proposals`
    composes both — and what this seam adds is the checks that need the whole
    set: element references resolving against the System Model, IDs unique
    across it, and every grounds entry resolving and, for a quote, actually
    appearing in the source it names.

    **Whether a draft sits in the right lane is not among them, and cannot be.**
    A draft's lane is stamped from the node by
    :func:`~stride_service.evidence.resolve_proposals` rather than written by
    the agent, so comparing the two would compare a value against the value it
    was copied from. The question that survives is whether the claim *belongs*
    in the lane it was found in, which is about the finding's content rather
    than its serialization, and is a critic's judgement step.

    Two things it *marks* rather than fails on, because the fan-in has no
    re-ask path and a whole report is too much to trade for either: a quote
    absent from the source it names, and an element ID a description cites in
    prose that the model does not contain
    (:class:`~stride_service.report.UnresolvedMention`). A claim whose every
    ground is such a quote is *dropped* and marked
    (:class:`~stride_service.report.GroundlessClaim`), on the same trade. A
    package's record adds whatever else its own judgement fields earn
    (:meth:`~stride_service.report.Claim.claim_marks`).

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
    known_ids = {element.id for element in system_model.elements()}
    merged = snap_drafts(
        [draft for lane in package.lanes for draft in drafts_by_lane.get(lane, ())],
        known_ids,
        sources.keys(),
    )
    issues = (
        _duplicate_id_issues(merged)
        + _unresolved_reference_issues(merged, system_model)
        + _ground_reference_issues(merged, system_model, sources)
    )
    if issues:
        raise DraftJoinError("; ".join(issues))
    # Only once the references resolve: a quote naming a source the job never
    # carried has already failed above, and matching its text against the empty
    # string would report the same defect a second time in worse words.
    unverified, groundless = _verify_quotes(merged, sources) if sources else ([], [])
    dropped = {mark.claim_id for mark in groundless}
    kept = [draft for draft in merged if draft.id not in dropped]
    return JoinedDrafts(
        drafts=kept,
        marks=AnalysisMarks(
            unverified_grounds=unverified,
            unresolved_mentions=_unresolved_mentions(kept, known_ids),
            groundless_claims=groundless,
        ).merged_with(package.record.claim_marks(kept)),
    )


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
    :class:`~stride_service.report.Ruling` carries no pattern to fire first and
    fatally.

    Element references are deliberately **not** checked: a ruling carries none.
    They are the join seam's business (:func:`join_drafts` fails closed on a
    draft citing an element the model does not contain), and since the critic
    no longer re-emits them there is no second place they can break. An issue
    listed here has to be one the re-ask can actually fix, and a draft's bad
    reference never was.
    """
    drafted_ids = {draft.id for draft in drafts}
    ruled_ids = {ruling.id for ruling in rulings}
    dropped = sorted(drafted_ids - ruled_ids)
    per_ruling = _verdict_shape_issues(rulings) + _unresolved_unknown_ref_issues(
        rulings, system_model
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
    return ReviewProblems(messages=messages, implicated=frozenset(implicated))


def _ruled(draft: Claim, ruling: Ruling, ruled_record: type[RuledClaim]) -> RuledClaim:
    """One draft plus the critic's ruling on it, as the package's ruled record.

    The draft's own fields are carried across from the copy the service already
    held rather than from anything the critic emitted, so a review cannot alter
    a description or an element reference.

    **What a ruling may replace is stated by the ruling's own shape, not by a
    list here.** Every field a package's :class:`~stride_service.report.Ruling`
    subclass declares beyond ``id`` and ``verdict`` is merged onto the draft, and
    a field holding ``None`` leaves the draft's alone. That one rule covers both
    of the things a package actually does with those fields: STRIDE's
    ``confidence`` is a judgement the draft never had and is required, so it
    always lands; STRIDE's ``severity`` is a draft field the calibration step may
    replace, and its ``None`` — the common case — keeps the agent's rating and the
    justification that argues for it together.

    The verdict is **rebuilt** rather than carried across, promoting the
    critic's unruled :class:`~stride_service.report.ProposedVerdict` to the
    :class:`~stride_service.report.Verdict` the report defines. It cannot fail:
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

    rulings = snap_rulings(rulings, {element.id for element in system_model.elements()})
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
    :meth:`~stride_service.frameworks.FrameworkPackage.carries_severity` reads
    it: a framework that grades nothing has every claim on one rank and falls
    through to the ID, which is a stable order rather than the critic's emission
    order.
    """
    severity = getattr(claim, "severity", None)
    rank = SEVERITY_ORDER.index(severity.level) if severity is not None else 0
    return rank, claim.id
