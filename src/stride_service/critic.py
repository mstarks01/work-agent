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

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import NamedTuple

from stride_service.grounding import verify_quote
from stride_service.report import (
    STRIDE_CATEGORIES,
    DraftThreat,
    Ground,
    SeverityLevel,
    StrideCategory,
    Threat,
    ThreatRuling,
    UnverifiedGround,
)
from stride_service.system_model import Element, SystemModel

# Most severe first — the order the report's ``threats`` array carries.
SEVERITY_ORDER: tuple[SeverityLevel, ...] = ("critical", "high", "medium", "low")


class DraftJoinError(ValueError):
    """The merged category agents' drafts fail a mechanical check."""


class CriticOutputError(ValueError):
    """The critic's output does not account for exactly the drafts it saw."""


class AssembledThreats(NamedTuple):
    """The critic's ruled threats, split into the report's two arrays."""

    threats: list[Threat]
    rejected_threats: list[Threat]


class JoinedDrafts(NamedTuple):
    """What the fan-in produces: the merged drafts, and what did not verify.

    Two returns rather than one because they have different owners. The drafts
    are the agents'; ``unverified`` is the *service's* record of quotes it
    looked for and could not find, which is why it rides beside the drafts
    instead of on them.
    """

    drafts: list[DraftThreat]
    unverified: list[UnverifiedGround]


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
    for ruling in rulings:
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
    threats: Iterable[DraftThreat], sources: Mapping[str, str]
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
    for threat in threats:
        unverified = [
            index
            for index, ground in enumerate(threat.grounds)
            if ground.kind == "quote"
            and not verify_quote(ground.text, sources.get(ground.source_label, ""))
        ]
        if len(unverified) == len(threat.grounds):
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
        raise DraftJoinError("; ".join(issues))
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

    The fan-in is where this belongs because it is the first point at which all
    six lanes' drafts, the System Model and the job's sources exist together.

    ``sources`` maps each source's label to its text. It defaults to empty for
    the same reason the validity gate's citation rule takes its label set as a
    parameter: a hand-authored model driven through the in-process engine has
    no sources to check against, and inventing a set would fail it on a
    citation that is not wrong. Empty means the text check does not run — no
    quote is marked and no threat fails on one.
    """
    merged = [
        draft
        for category in STRIDE_CATEGORIES
        for draft in drafts_by_category.get(category, ())
    ]
    misfiled = [
        f"draft {draft.id!r} is filed under {category!r} but its category is"
        f" {draft.category!r}"
        for category, drafts in drafts_by_category.items()
        for draft in drafts
        if draft.category != category
    ]
    issues = (
        misfiled
        + _duplicate_id_issues(merged)
        + _unresolved_reference_issues(merged, system_model)
        + _ground_reference_issues(merged, system_model, sources)
    )
    if issues:
        raise DraftJoinError("; ".join(issues))
    # Only once the references resolve: a quote naming a source the job never
    # carried has already failed above, and matching its text against the empty
    # string would report the same defect a second time in worse words.
    unverified = _verify_quotes(merged, sources) if sources else []
    return JoinedDrafts(drafts=merged, unverified=unverified)


def review_issues(
    drafts: Sequence[DraftThreat],
    rulings: Sequence[ThreatRuling],
    system_model: SystemModel,
) -> ReviewProblems:
    """Every way the critic's rulings fail to account for the drafts it saw.

    The mechanical check, returned rather than raised, so the graph can *route*
    on it: a falsy result means the rulings are assemblable, a truthy one is
    what the bounded re-ask is asked to fix. The critic must
    rule on exactly the drafted set — no threat invented, none dropped — with
    unique IDs, and each ``needs-info`` verdict naming only unknowns the model
    actually contains. Verdict shape and the ID's category letter are enforced
    by :class:`~stride_service.report.ThreatRuling` itself and never re-checked
    here.

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
    unresolved = _unresolved_unknown_ref_issues(rulings, system_model)
    messages = [f"critic dropped draft {threat_id!r}" for threat_id in dropped]
    messages += [
        f"critic returned threat {threat_id!r}, which no category agent drafted"
        for threat_id in sorted(ruled_ids - drafted_ids)
    ]
    messages += _duplicate_id_issues(rulings)
    messages += [issue.message for issue in unresolved]
    # A duplicate ID implicates no draft: the re-ask drops one of two rulings on
    # an ID it already ruled, which is answerable from the rulings alone. An
    # invented ID implicates none either — there is no draft behind it to show.
    implicated = set(dropped) | {
        issue.threat_id for issue in unresolved if issue.threat_id in drafted_ids
    }
    return ReviewProblems(messages=messages, implicated=frozenset(implicated))


def _ruled(draft: DraftThreat, ruling: ThreatRuling) -> Threat:
    """One draft plus the critic's ruling on it, as the report's :class:`Threat`.

    The draft's own fields are carried across from the copy the service already
    held rather than from anything the critic emitted, so a review cannot alter
    a description or an element reference. ``severity`` is the single exception
    and the single overridable field: the calibration step is allowed to replace
    a rating, and does so together with the justification that argues for it.
    """
    return Threat(
        **draft.model_dump(exclude={"severity"}),
        severity=ruling.severity or draft.severity,
        confidence=ruling.confidence,
        verdict=ruling.verdict,
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

    ruling_by_id = {ruling.id: ruling for ruling in rulings}
    reviewed = [_ruled(draft, ruling_by_id[draft.id]) for draft in drafts]
    actionable = [t for t in reviewed if t.verdict.status != "rejected"]
    rejected = [t for t in reviewed if t.verdict.status == "rejected"]
    return AssembledThreats(sorted(actionable, key=_severity_key), rejected)


def _severity_key(threat: Threat) -> tuple[int, str]:
    return SEVERITY_ORDER.index(threat.severity.level), threat.id
