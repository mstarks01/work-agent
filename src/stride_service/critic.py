"""Mechanical checks at the join and assemble seams around the critic.

The deterministic half of the critic step: mechanical checks belong in code,
prompts carry only judgement. Everything here is a check no model should be
asked to perform — that the six category agents' drafts cite elements the System
Model actually contains, that threat IDs are unique, that every grounds entry
resolves and every quote ground is really in the source it names, and that the
critic ruled on exactly the drafts it was given, each ruling carrying a
well-formed verdict. The critic prompt names these as already done so its
judgement is spent on evidence, lanes, duplicates, severity and confidence
instead — and, for grounds, on the one question code cannot answer: whether a
quote that is verbatim actually *supports* the finding it was filed under.

The assemble seam is also where a ruling becomes a threat. The critic emits
judgements keyed by draft ID rather than the drafts themselves
(:class:`~stride_service.report.ThreatRuling`), so the agent's own fields
reach the report from the copy this service already holds — not round-tripped
through a model that was never asked to change them.

Model output is untrusted input (OWASP LLM05): it is validated here, before
anything reaches the report. Both seams fail closed with every issue listed
at once — an agent that hallucinates an element ID or a critic that drops
threats is a defect to surface loudly, never to paper over by discarding the
offending entries.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import NamedTuple, get_args

from stride_service.grounding import verify_quote
from stride_service.references import canonical, snap
from stride_service.report import (
    CATEGORY_LETTERS,
    STRIDE_CATEGORIES,
    DraftThreat,
    Ground,
    MissingMitigation,
    SeverityLevel,
    StrideCategory,
    Threat,
    ThreatRuling,
    UnresolvedMention,
    UnverifiedGround,
    Verdict,
)
from stride_service.system_model import DataFlow, Element, SystemModel

# Most severe first — the order the report's ``threats`` array carries.
SEVERITY_ORDER: tuple[SeverityLevel, ...] = ("critical", "high", "medium", "low")


class DraftJoinError(ValueError):
    """The merged category agents' drafts fail a mechanical check."""


class GroundsUnverifiedError(DraftJoinError):
    """Threats on which *no* ground verified — the fail-closed half of the
    grounding policy.

    A subclass rather than a distinctive message, because the faults it has to
    be told apart from are genuinely different faults. A dangling element
    reference or an unresolvable ``source_label`` is a draft citing something
    that does not exist; this is a draft citing something that does exist and
    quoting it wrongly. Only the second says anything about how often a model
    fabricates a span, and a reader that has to pattern-match the prose to tell
    them apart is guessing rather than measuring.

    Both halves of that rate ride on the exception, because the raise is the
    only moment they coexist: ``threat_ids`` are the threats that lost every
    ground, and ``draft_count`` is the population they came from. Nothing
    downstream of here sees either — the job is over.
    """

    def __init__(
        self, message: str, *, threat_ids: Sequence[str], draft_count: int
    ) -> None:
        super().__init__(message)
        self.threat_ids = tuple(threat_ids)
        self.draft_count = draft_count


class CriticOutputError(ValueError):
    """The critic's output does not account for exactly the drafts it saw."""


class AssembledThreats(NamedTuple):
    """The critic's ruled threats, split into the report's two arrays."""

    threats: list[Threat]
    rejected_threats: list[Threat]


class JoinedDrafts(NamedTuple):
    """What the fan-in produces: the merged drafts, and what did not check out.

    Four returns rather than one because they have different owners. The drafts
    are the agents'; the three mark lists are the *service's* record of what
    each draft failed to make good on — a quote that is not in the source it
    named, an element ID a description cited that does not exist, a threat
    offering no countermeasure and no reason for offering none. All three ride
    beside the drafts rather than on them, because a field an agent could set
    about its own accuracy is not evidence of it.

    None of the three is fatal, and that is the whole policy of this seam:
    checks that decide whether a finding *means* anything fail closed, and
    checks that describe how complete it is are recorded for a reader. The
    fan-in has no re-ask path, so the second kind must never cost a report.
    """

    drafts: list[DraftThreat]
    unverified: list[UnverifiedGround]
    mentions: list[UnresolvedMention]
    unmitigated: list[MissingMitigation]


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

    Measured over the 18 hand-authored descriptions in ``prompts/exemplars/``,
    the closest thing the repo holds to real agent prose: **14 distinct IDs
    extracted, 0 of them spurious** — every token found is one of the exemplar
    system's 14 real element IDs, and all 14 are found. Small, and the only
    corpus of threat descriptions that exists; enough to say the pattern reads
    prose without inventing citations in it.

    Trailing hyphens are trimmed because prose runs an ID into an em-dash
    substitute more often than a real slug ends in one; ``normalize_name``
    strips them, so no legal ID ends in a hyphen anyway.
    """
    return [match.group().rstrip("-") for match in _MENTION_RE.finditer(description)]


def _unresolved_mentions(
    threats: Iterable[DraftThreat], element_ids: Collection[str]
) -> list[UnresolvedMention]:
    """Marks for every ID a description cites that the model does not contain."""
    return [
        UnresolvedMention(threat_id=threat.id, mention=mention)
        for threat in threats
        for mention in mentioned_ids(threat.description)
        if not canonical(mention, element_ids)
    ]


def _missing_mitigations(
    threats: Iterable[DraftThreat],
) -> list[MissingMitigation]:
    """Marks for threats offering no countermeasure and no reason for none.

    The prompt licenses an empty ``mitigations`` for exactly one case — the
    threat is conditional on an ``unknown`` and no countermeasure can be named
    without first learning that fact — and step 6's branch rule makes that case
    recognizable: a threat triggered by an unknown carries an
    ``unknown-attribute`` ground, because the trigger dictates the branch. So
    the licensed empty and the unlicensed one are told apart by the draft's own
    grounds, with no judgement asked of anybody.
    """
    return [
        MissingMitigation(threat_id=threat.id)
        for threat in threats
        if not threat.mitigations
        and not any(ground.kind == "unknown-attribute" for ground in threat.grounds)
    ]


def numbering_gaps(threats: Iterable[DraftThreat]) -> list[str]:
    """Every lane whose drafts are not numbered ``01..N``, as messages.

    The prompt asks each agent for "a two-digit sequence starting at ``01``,
    numbered within your category only". A lane that emits ``S-01, S-02, S-05``
    has not followed it.

    **Returned rather than raised, and deliberately not on the report.** A gap
    breaks nothing: IDs are opaque handles, they are unique, their letters
    match, and every downstream check passes. Renumbering would make them
    comply, and is refused — rewriting a finding's identity to satisfy a
    cosmetic rule is a bigger liberty than the rule is worth, and it would move
    a threat's ID between two runs of the same input.

    So this is a signal about the *agents*, not about the report, and it is
    logged where the other operational facts about a run are rather than shown
    to a reader who can do nothing with it. Worth having because it is free and
    because nothing else in the graph would ever mention it: an agent quietly
    drifting from its output contract is the kind of thing that is obvious in
    hindsight and invisible in advance.
    """
    numbers_by_category: dict[StrideCategory, list[int]] = {}
    for threat in threats:
        numbers_by_category.setdefault(threat.category, []).append(
            int(threat.id.split("-", 1)[1])
        )
    return [
        f"the {category} lane numbered its {len(numbers)} drafts"
        f" {', '.join(sorted(f'{CATEGORY_LETTERS[category]}-{n:02d}' for n in numbers))},"
        f" not 01..{len(numbers):02d}"
        for category, numbers in numbers_by_category.items()
        if sorted(numbers) != list(range(1, len(numbers) + 1))
    ]


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
    if ground.kind == "unknown-attribute":
        return ground.model_copy(
            update={"element_id": snap(ground.element_id, element_ids)}
        )
    return ground.model_copy(update={"flow_id": snap(ground.flow_id, element_ids)})


def snap_drafts(
    drafts: Iterable[DraftThreat],
    element_ids: Collection[str],
    source_labels: Collection[str] = (),
) -> list[DraftThreat]:
    """Every reference a draft carries, in the spelling the job holds.

    Run at the fan-in before the checks, so what those checks compare — and what
    the report goes on to carry — is the canonical spelling rather than whatever
    each of six independently-vendored agents happened to type. The drafts reach
    the report unaltered otherwise: this rewrites references and nothing else.

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
    rulings: Iterable[ThreatRuling], element_ids: Collection[str]
) -> list[ThreatRuling]:
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
    threats: Iterable[DraftThreat], system_model: SystemModel
) -> list[str]:
    known_ids = {element.id for element in system_model.elements()}
    return [
        f"threat {threat.id!r} references element {ref!r}, which is not in the"
        " system model"
        for threat in threats
        for ref in threat.affected_element_ids
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
    """One unresolved-unknown problem, and the threat whose ruling carries it.

    The ID rides beside the message because two callers need different halves.
    The re-ask prompt reads the *message*; the seam that builds that prompt
    reads the *ID*, to decide which drafts the re-ask has to be shown in full.
    Recovering the ID by parsing it back out of the message would make the
    wording of an error string load-bearing for what the graph sends a model.
    """

    threat_id: str
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


def _verdict_shape_issues(rulings: Iterable[ThreatRuling]) -> list[CriticIssue]:
    """Every ruling whose verdict's fields disagree with its own ``status``.

    The three rules :class:`~stride_service.report.Verdict` states, asked here
    rather than in the schema. The schema is the wrong place for them twice
    over: a provider cannot be made to enforce a dependency between fields, and
    a validator that raises does so at the node boundary, killing the critic
    node — one pass over every draft in the job — with the re-ask that exists
    for exactly this class of problem still unreached.

    So they are returned, like every other problem this module finds, and the
    router sends them to ``recritic``. Each names its threat, because the fix
    is per-ruling: a reason to write, an unknown to name, or a list to drop.

    Deliberately three separate messages rather than one per ruling. A critic
    that rejected a threat without a reason *and* attached unknowns to it has
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
                    f"threat {ruling.id!r} is ruled needs-info but names no"
                    " unknown attribute in related_unknowns, so nothing says"
                    " what has to be answered",
                )
            )
        if verdict.status != "needs-info" and verdict.related_unknowns:
            issues.append(
                CriticIssue(
                    ruling.id,
                    f"threat {ruling.id!r} is ruled {verdict.status} but carries"
                    " related_unknowns, which is only meaningful on a"
                    " needs-info verdict",
                )
            )
        if verdict.status != "confirmed" and not verdict.reason:
            issues.append(
                CriticIssue(
                    ruling.id,
                    f"threat {ruling.id!r} is ruled {verdict.status} and states"
                    " no reason",
                )
            )
    return issues


def _unresolved_unknown_ref_issues(
    rulings: Iterable[ThreatRuling], system_model: SystemModel
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
                        f"threat {ruling.id!r} hangs its needs-info verdict on"
                        f" element {ref.element_id!r}, which is not in the system"
                        " model",
                    )
                )
            elif ref.attribute not in _attribute_names(element):
                issues.append(
                    CriticIssue(
                        ruling.id,
                        f"threat {ruling.id!r} hangs its needs-info verdict on"
                        f" attribute {ref.attribute!r}, which element"
                        f" {ref.element_id!r} does not have",
                    )
                )
    return issues


def _duplicate_id_issues(entries: Iterable[DraftThreat | ThreatRuling]) -> list[str]:
    counts = Counter(entry.id for entry in entries)
    return [
        f"threat ID {threat_id!r} is used by {count} drafts"
        for threat_id, count in counts.items()
        if count > 1
    ]


def _ground_reference_issues(
    threats: Iterable[DraftThreat],
    system_model: SystemModel,
    source_labels: Collection[str],
) -> list[str]:
    """Every grounds entry whose reference does not resolve.

    Set membership, one branch at a time: a quote's ``source_label`` against
    the job's labels, an unknown-attribute's ``element_id`` and ``attribute``
    against the model, a derived-fact's ``flow_id`` against the crossings
    derived from that same model. The gate's own stated principle is what puts
    it here — *set membership is mechanical, so it belongs in code rather than
    in a prompt* — and it inherits that rule's escape: where a job supplies no
    labels, the label half does not run, so a hand-authored model driven
    through the in-process engine is not failed on a citation that is not
    wrong.
    """
    by_id = {element.id: element for element in system_model.elements()}
    crossing_ids = {crossing.flow_id for crossing in system_model.boundary_crossings()}
    legal_labels = frozenset(source_labels)
    issues = []
    for threat in threats:
        for ground in threat.grounds:
            issues += _one_ground_issues(
                threat.id, ground, by_id, crossing_ids, legal_labels
            )
    return issues


def _one_ground_issues(
    threat_id: str,
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
                f"threat {threat_id!r} grounds a quote in source"
                f" {ground.source_label!r}, which is not one of this job's"
                f" sources {sorted(legal_labels)}"
            )
    elif ground.kind == "derived-fact":
        if ground.flow_id not in crossing_ids:
            issue = (
                f"threat {threat_id!r} grounds a derived fact in flow"
                f" {ground.flow_id!r}, which is not a derived boundary crossing"
            )
    else:
        element = by_id.get(ground.element_id)
        if element is None:
            issue = (
                f"threat {threat_id!r} grounds an unknown attribute on element"
                f" {ground.element_id!r}, which is not in the system model"
            )
        elif ground.attribute not in _attribute_names(element):
            issue = (
                f"threat {threat_id!r} grounds an unknown attribute"
                f" {ground.attribute!r}, which element {ground.element_id!r}"
                " does not have"
            )
    return [issue] if issue else []


def _verify_quotes(
    threats: Sequence[DraftThreat], sources: Mapping[str, str]
) -> list[UnverifiedGround]:
    """Check every quote ground against the source it names.

    Two outcomes, at two different scopes, and the split is the whole policy.
    **Per entry**, an unverifiable quote is *marked* and still renders: 0
    failures in 206 measured excerpts is not evidence of zero, and the Rule of
    Three puts the 95% bound at 1.46% per quote — which at the corpus mean of
    18.7 threats per job is a 24% chance that some job dies on a single
    cosmetic mismatch. That is not enough evidence to license killing a job.

    **Per threat**, if *no* ground verifies at all, the caller fails closed. A
    threat with one bad quote beside good ones is still justified; a threat
    where nothing holds is a finding with no machine-checkable justification.
    That total loss is rarer than it sounds — unknown-attribute and
    derived-fact grounds verify by set membership, which is deterministic and
    always available, so a threat can only lose every ground if every one of
    them is a quote and every quote is bad.

    Nothing is filtered: this marks and it fails, and it removes neither an
    entry nor a threat from anything.
    """
    marks: list[UnverifiedGround] = []
    issues: list[str] = []
    failed: list[str] = []
    for threat in threats:
        unverified = [
            index
            for index, ground in enumerate(threat.grounds)
            if ground.kind == "quote"
            and not verify_quote(ground.text, sources.get(ground.source_label, ""))
        ]
        if len(unverified) == len(threat.grounds):
            failed.append(threat.id)
            issues.append(
                f"threat {threat.id!r} has no ground that verifies: all"
                f" {len(unverified)} of its quotes are absent from the sources"
                " they name"
            )
            continue
        marks += [
            UnverifiedGround(
                threat_id=threat.id,
                index=index,
                reason=f"not found in {threat.grounds[index].source_label!r}",
            )
            for index in unverified
        ]
    if issues:
        raise GroundsUnverifiedError(
            "; ".join(issues), threat_ids=failed, draft_count=len(threats)
        )
    return marks


def join_drafts(
    drafts_by_category: Mapping[StrideCategory, Sequence[DraftThreat]],
    system_model: SystemModel,
    sources: Mapping[str, str] = MappingProxyType({}),
) -> JoinedDrafts:
    """Merge the six category agents' drafts into the single list the critic sees.

    Canonical STRIDE order, so the critic reads the lanes in the same order
    every run. Category letters and each ``Ground``'s own shape are enforced by
    :class:`DraftThreat` itself; what this seam adds is the checks that need the
    whole set — element references resolving against the System Model, IDs
    unique across it, and every grounds entry resolving and, for a quote,
    actually appearing in the source it names.

    **Whether a draft sits in the right lane is not among them, and cannot be.**
    A draft's category is stamped from the lane by
    :func:`~stride_service.evidence.resolve_proposals` rather than written by
    the agent, so comparing the two would compare a value against the value it
    was copied from. The question that survives is whether the threat *belongs*
    in the lane it was found in, which is about the finding's content rather
    than its serialization, and is the critic's second judgement step.

    Two things it *marks* rather than fails on, because the fan-in has no
    re-ask path and a whole report is too much to trade for either: a quote
    absent from the source it names, and an element ID a description cites in
    prose that the model does not contain
    (:class:`~stride_service.report.UnresolvedMention`).

    References are snapped to their canonical spelling first
    (:func:`~stride_service.references.snap_drafts`), so the checks below —
    and the report, which carries these drafts' own fields through unaltered —
    see the spelling the job holds rather than each agent's. That is
    recognition and never resolution: a reference naming nothing is left
    exactly as written, for the check to report in the agent's own words.

    The fan-in is where this belongs because it is the first point at which all
    six lanes' drafts, the System Model and the job's sources exist together.

    ``sources`` maps each source's label to its text. It defaults to empty for
    the same reason the validity gate's citation rule takes its label set as a
    parameter: a hand-authored model driven through the in-process engine has
    no sources to check against, and inventing a set would fail it on a
    citation that is not wrong. Empty means the text check does not run — no
    quote is marked and no threat fails on one.
    """
    known_ids = {element.id for element in system_model.elements()}
    merged = snap_drafts(
        [
            draft
            for category in STRIDE_CATEGORIES
            for draft in drafts_by_category.get(category, ())
        ],
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
    unverified = _verify_quotes(merged, sources) if sources else []
    return JoinedDrafts(
        drafts=merged,
        unverified=unverified,
        mentions=_unresolved_mentions(merged, known_ids),
        unmitigated=_missing_mitigations(merged),
    )


def review_issues(
    drafts: Sequence[DraftThreat],
    rulings: Sequence[ThreatRuling],
    system_model: SystemModel,
) -> ReviewProblems:
    """Every way the critic's rulings fail to account for the drafts it saw.

    The mechanical check, returned rather than raised, so the graph can *route*
    on it: a falsy result means the rulings are assemblable, a truthy one is
    what the bounded re-ask is asked to fix. The critic must rule on exactly the
    drafted set — no threat invented, none dropped — with unique IDs, with each
    verdict carrying the fields its own ``status`` calls for, and each
    ``needs-info`` naming only unknowns the model actually contains.

    **Verdict shape is checked here rather than by the schema**, and that is
    the reason this function is worth reading twice. The rules are conditional
    on ``status``, which no provider schema can express, so they can only be
    enforced after the fact — and enforcing them in a pydantic validator means
    enforcing them at the node boundary, where a raise kills the critic node
    and the whole job with it. Every other problem in this list gets a bounded
    re-ask; a missing reason is not a worse fault than a dropped draft, and
    there is no reason for it to be the fatal one. The ID's category letter
    stays on :class:`~stride_service.report.ThreatRuling` — a pattern on a
    single field is something a schema *can* carry.

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
    messages = [f"critic dropped draft {threat_id!r}" for threat_id in dropped]
    messages += [
        f"critic returned threat {threat_id!r}, which no category agent drafted"
        for threat_id in sorted(ruled_ids - drafted_ids)
    ]
    messages += _duplicate_id_issues(rulings)
    messages += [issue.message for issue in per_ruling]
    # A duplicate ID implicates no draft: the re-ask drops one of two rulings on
    # an ID it already ruled, which is answerable from the rulings alone. An
    # invented ID implicates none either — there is no draft behind it to show.
    #
    # Every per-ruling problem does implicate one. Naming the unknown a
    # needs-info hangs on, or writing the reason a rejection owes a reader,
    # cannot be done from an ID — both are claims about a specific threat, and
    # a re-ask that cannot read it would have to invent one.
    implicated = set(dropped) | {
        issue.threat_id for issue in per_ruling if issue.threat_id in drafted_ids
    }
    return ReviewProblems(messages=messages, implicated=frozenset(implicated))


def _ruled(draft: DraftThreat, ruling: ThreatRuling) -> Threat:
    """One draft plus the critic's ruling on it, as the report's :class:`Threat`.

    The draft's own fields are carried across from the copy the service already
    held rather than from anything the critic emitted, so a review cannot alter
    a description or an element reference. ``severity`` is the single exception
    and the single overridable field: the calibration step is allowed to replace
    a rating, and does so together with the justification that argues for it.

    The verdict is **rebuilt** rather than carried across, promoting the
    critic's unruled :class:`~stride_service.report.ProposedVerdict` to the
    :class:`~stride_service.report.Verdict` the report defines. It cannot fail:
    :func:`review_issues` has already passed on exactly these rulings, and its
    three verdict checks are that model's validator asked one seam earlier. A
    raise here would mean the two had drifted, which is why the promotion is
    left able to raise rather than coerced.
    """
    return Threat(
        **draft.model_dump(exclude={"severity"}),
        severity=ruling.severity or draft.severity,
        confidence=ruling.confidence,
        verdict=Verdict.model_validate(ruling.verdict.model_dump()),
    )


def assemble_threats(
    drafts: Sequence[DraftThreat],
    rulings: Sequence[ThreatRuling],
    system_model: SystemModel,
) -> AssembledThreats:
    """Merge the critic's rulings onto the drafts, split into the report's arrays.

    :func:`review_issues` is the gate — one definition of what "well-formed
    critic output" means, shared with the router that decides whether to
    re-ask. Assembly runs only after that gate has passed, but re-checks here
    and fails closed regardless: nothing reaches the report on output that did
    not survive the check. Rejected threats ride in their own audit array; the
    rest are sorted most-severe-first, as the report expects.

    Threats are built in ``drafts`` order — canonical STRIDE order, as
    :func:`join_drafts` left them — so the audit array does not inherit
    whatever order the critic happened to emit its rulings in.
    """
    problems = review_issues(drafts, rulings, system_model)
    if problems:
        raise CriticOutputError("; ".join(problems.messages))

    rulings = snap_rulings(rulings, {element.id for element in system_model.elements()})
    ruling_by_id = {ruling.id: ruling for ruling in rulings}
    reviewed = [_ruled(draft, ruling_by_id[draft.id]) for draft in drafts]
    actionable = [t for t in reviewed if t.verdict.status != "rejected"]
    rejected = [t for t in reviewed if t.verdict.status == "rejected"]
    return AssembledThreats(sorted(actionable, key=_severity_key), rejected)


def _severity_key(threat: Threat) -> tuple[int, str]:
    return SEVERITY_ORDER.index(threat.severity.level), threat.id
