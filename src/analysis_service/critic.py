"""Mechanical checks around a framework's critic: what it reads, and what it ruled.

This is the deterministic half of the critic step. Mechanical checks belong in
code, and prompts carry only judgement. Everything here is a check no model
should be asked to perform: that the critic ruled on exactly the drafts it was
given, each ruling carrying a well-formed verdict and each ``needs-info``
naming an unknown the model contains; that a re-ask changed only what the
problems named; and, at assembly, that a ruling becomes a claim from the copy
this service already holds. The view a critic reads is built here too, with
the pairs it would otherwise hunt for computed onto it, so the first pass and
the re-ask cannot disagree about what a critic sees.

The checks that run *before* a critic reads anything — element references,
unique IDs, grounds that resolve, quotes that are in the source they name —
are the fan-in's, in :mod:`analysis_service.fan_in`. A package's critic prompt
names those as already done, so its judgement is spent on evidence, lanes,
duplicates and whatever else that framework grades. For grounds, it is spent on
the one question code cannot answer: whether a quote that is verbatim actually
supports the finding it was filed under.

The checks are neutral, and there is one seam per framework rather than one
across frameworks. Every check here reads
:class:`~analysis_service.report.Claim`,
:class:`~analysis_service.report.Ruling` and the package contract, so a second
framework's output goes through the same code.

The assemble seam is where a ruling becomes a claim. A critic emits judgements
keyed by draft ID rather than the drafts themselves, as a
:class:`~analysis_service.report.Ruling`. The agent's own fields therefore reach
the report from the copy this service already holds, rather than round-tripping
through a model that was never asked to change them.

Model output is untrusted input (OWASP LLM05), and the service validates it here
before anything reaches the report. The review check is returned rather than
raised, so the graph can route a malformed first pass to its bounded re-ask
(ADR 0005); assembly fails closed, and lists every issue at once.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NamedTuple

from analysis_service.frameworks import FrameworkSchemas, lane_of
from analysis_service.references import snap
from analysis_service.report import (
    Claim,
    RepairedQuote,
    RuledClaim,
    Ruling,
    SeverityLevel,
    UnknownRef,
    Verdict,
)
from analysis_service.system_model import (
    ModelIndex,
    SystemModel,
    TrustBoundary,
    attribute_names,
)

# Most severe first — the order a graded framework's ``claims`` array carries.
# The service holds the order because it holds
# :data:`~analysis_service.report.SeverityLevel`; whether a framework grades at all
# is its record's business, and one that does not is ordered by ID alone.
SEVERITY_ORDER: tuple[SeverityLevel, ...] = ("critical", "high", "medium", "low")


class CriticOutputError(ValueError):
    """The critic's output does not account for exactly the drafts it saw."""


class AssembledClaims(NamedTuple):
    """One critic's ruled claims, split into that block's two arrays."""

    claims: list[RuledClaim]
    rejected_claims: list[RuledClaim]


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
    #: Every *drafted* ID some problem names: ``implicated`` plus the IDs the
    #: critic ruled twice. It is the whole of what a re-ask may change; a
    #: ruling on any other drafted ID is already correct and is kept from the
    #: first pass by :func:`merge_retry`, whatever the re-ask returns for it.
    repairable: frozenset[str] = frozenset()

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
                    " reasoning, lane or duplicate ended it",
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

    The ``attribute`` half is the element type's security-relevant fields —
    :func:`~analysis_service.system_model.attribute_names`, the list the
    evidence catalog walks — so ``name``, ``notes`` and ``description`` are
    refused here exactly as no catalog entry is ever written for them. This
    reader used to accept every pydantic field on the element, and the live
    critic answered with a needs-info on ``notes`` in 14 of the 38 reports
    archived under ``evals/runs/``: a question about a sentence, pointed at a
    field that happened to resolve. The check stops at the attribute's
    existence. Requiring the named attribute to actually *hold* the ``unknown``
    sentinel was refused: it encodes a judgement as a mechanical rule and
    misfires on a stated-but-vague value — "some encryption" in
    ``encryption_at_rest``, where a needs-info is legitimate and the field is
    not literally ``unknown``.

    A ``Ground``'s unknown-attribute branch is spelled identically to
    :class:`~analysis_service.report.UnknownRef` and is checked one step
    deeper, by catalog membership
    (:func:`~analysis_service.evidence.ground_issues`). The two differ because
    their writers differ: a ground is written by code out of the catalog, so
    "is it in the catalog" is the invariant the writer held; a ref is written
    by the critic, whose question about a stated value is a judgement. The
    blast radius differs too: a failure here lands in ``review_issues`` and
    routes to the bounded ``recritic`` re-ask, where a grounds failure kills
    the job outright.
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
            elif ref.attribute not in attribute_names(element):
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
                        f" {', '.join(attribute_names(element))}."
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


def _duplicate_on_unit_issues(
    drafts: Sequence[Claim], rulings: Iterable[Ruling]
) -> list[CriticIssue]:
    """Every ``duplicate`` rejection of a draft that rules on a unit of its own.

    A package whose drafts name a unit — a catalog requirement — decides
    duplication by that unit's identifier, and :func:`~analysis_service.fan_in._drop_duplicate_ids` has
    already dropped the second copy before any critic reads the set. So a
    ``duplicate`` rejection here can only mean the critic judged two rulings on
    two *different* requirements to be one concern, which is a ruling the
    standard does not have: each requirement is its own question. The re-ask
    asks for the answer to that question instead. A package whose drafts name
    no unit is untouched, because there the critic is the only reader of
    duplication.
    """
    unit_by_id = {draft.id: type(draft).unit_of(draft) for draft in drafts}
    return [
        CriticIssue(
            ruling.id,
            f"claim {ruling.id!r} rules on {unit_by_id[ruling.id]!r}, and a"
            " duplicate of a unit-bearing draft is decided by its identifier"
            " before you see it, so rejecting it as a duplicate of another"
            " requirement names no check: rule on this requirement, or reject it"
            " for evidence with the fact that rules it out",
        )
        for ruling in rulings
        if ruling.verdict.status == "rejected"
        and ruling.verdict.rejected_because == "duplicate"
        and unit_by_id.get(ruling.id)
    ]


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
    """Each draft's ID against the other drafts naming one action at one place in its lane.

    The critic's duplicate step is a comparison of three fields — the lane, the
    verb and the endpoint-resolved targets — made here so the critic reads the
    pairs rather than hunting for them (#440). The lane is part of the key
    because it is part of a claim's identity: the corpus records one verb at
    one place in two lanes as two findings, and the critic prompt says two
    lanes are never duplicates. A read and a write of one flow carry two verbs,
    so they are never paired either.

    A draft with no verb belongs to a package whose identity is a catalog
    identifier, and its duplicates are ID collisions the join already refuses.
    """
    flows = ModelIndex.of(system_model).flow_endpoints
    by_key: dict[tuple[str | None, str, frozenset[str]], list[str]] = {}
    for draft in drafts:
        if draft.verb is None:
            continue
        key = (
            lane_of(draft),
            draft.verb,
            endpoint_targets(draft.affected_element_ids, flows),
        )
        by_key.setdefault(key, []).append(draft.id)
    return {
        draft_id: [other for other in ids if other != draft_id]
        for ids in by_key.values()
        if len(ids) > 1
        for draft_id in ids
    }


def rating_disagreements(drafts: Sequence[Claim]) -> dict[str, list[str]]:
    """Each draft against the others with one fact pattern and another rating.

    The STRIDE critic's rating step asks that identical fact patterns carry
    identical ratings across lanes. Two drafts with the same verb and the same set of catalogued
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
    They are the join seam's business (:func:`~analysis_service.fan_in.join_drafts`
    drops and marks a reference the model does not contain, and fails closed
    on a ground it cannot derive), and since the critic no longer re-emits
    them there is no second place they can break. An issue
    listed here has to be one the re-ask can actually fix, and a draft's bad
    reference never was.
    """
    rulings = complete_rulings(drafts, rulings)
    drafted_ids = {draft.id for draft in drafts}
    ruled_ids = {ruling.id for ruling in rulings}
    dropped = sorted(drafted_ids - ruled_ids)
    per_ruling = (
        _confirmed_on_unknown_issues(drafts, rulings)
        + _duplicate_on_unit_issues(drafts, rulings)
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
    duplicated = {
        claim_id
        for claim_id, count in Counter(ruling.id for ruling in rulings).items()
        if count > 1 and claim_id in drafted_ids
    }
    return ReviewProblems(
        messages=messages,
        implicated=frozenset(implicated),
        rulings=tuple(rulings),
        repairable=frozenset(implicated | duplicated),
    )


def merge_retry(
    first: Sequence[Mapping[str, Any]],
    retry: Sequence[Mapping[str, Any]],
    repairable: Collection[str],
    drafted: Collection[str],
) -> tuple[list[Mapping[str, Any]], list[str]]:
    """The re-ask's rulings for what the problems named, the first pass's for the rest.

    The re-ask prompt asks for exactly this and nothing enforced it: the first
    review and the re-ask wrote one state key, and the check that ran on the
    re-ask compared it with the drafts and never with the first pass. So a
    re-ask that changed a confirmed ruling to rejected while adding the one it
    dropped was accepted whole, and the change reached the report as if the
    review had reasoned it out.

    Over payloads the caller has already put in the ruling model's own
    spelling, because what is merged is what ``reviewed`` holds and what
    ``assemble`` reads back, and a comparison over two providers' spellings of
    one ruling would read a ``null`` against an absent key as a change. Two
    rules, and the second list says where the re-ask stepped outside them:

    * a drafted ID a problem named takes the re-ask's ruling, every copy of it,
      so a duplicated repair still fails the check that follows;
    * a drafted ID no problem named keeps the first pass's ruling, and a
      re-ask ruling that differs from it is recorded and discarded.

    An ID no lane agent drafted is not resolved here. The first pass's copy
    is left out, because removing it was the re-ask's whole instruction; a
    copy the re-ask returned anyway is passed through, so the check that
    follows names it and the job fails as it always did. A re-ask that
    invents is the service's defect, and a mark is not the place for one.
    """
    named = set(repairable)
    known = set(drafted)
    kept_first = {
        ruling["id"]: ruling
        for ruling in first
        if ruling["id"] in known and ruling["id"] not in named
    }
    merged: list[Mapping[str, Any]] = list(kept_first.values())
    drift: list[str] = []
    for ruling in retry:
        claim_id = ruling["id"]
        if claim_id in named or claim_id not in known:
            merged.append(ruling)
        elif ruling != kept_first.get(claim_id):
            drift.append(
                f"re-ask changed ruling {claim_id!r}, which no problem named;"
                " the first ruling was kept"
            )
    return merged, drift


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
    :func:`~analysis_service.fan_in.join_drafts` left them — so the audit array does not inherit
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
    repaired: Sequence[RepairedQuote] = (),
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
    repairs_by_claim: dict[str, list[dict]] = {}
    for mark in repaired:
        repairs_by_claim.setdefault(mark.claim_id, []).append(
            {"index": mark.index, "written": mark.written, "moved": mark.moved}
        )
    views = []
    for draft in drafts:
        view = draft.model_dump(
            mode="json",
            exclude={*_DRAFT_UNRULED_FIELDS, "framework", "framework_version"},
            exclude_defaults=True,
        )
        # Computed, never drafted: which quote grounds the service rewrote to
        # the source's own span, what the agent wrote, and what the rewrite
        # moved. The critic reads the span as the ground's text already; this
        # is what tells it the span is not the agent's evidence, and that a
        # negation or a number changed between the two.
        if draft.id in repairs_by_claim:
            view["repaired_quotes"] = repairs_by_claim[draft.id]
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
        # which is the pair the rating step calibrates across.
        if draft.id in rated_unlike:
            view["rated_unlike"] = list(rated_unlike[draft.id])
        # The framework's own words for the unit this draft rules on, so the
        # evidence step judges the description against the requirement rather
        # than against the draft's paraphrase of it (#659). Absent for a
        # framework whose claims are an open set.
        if text := type(draft).unit_text(draft):
            view["unit_text"] = text
        views.append(view)
    return views


def critic_view(
    drafts: Sequence[Claim],
    system_model: SystemModel,
    *,
    only: Collection[str] | None = None,
    repaired: Sequence[RepairedQuote] = (),
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

    ``repaired`` is the fan-in's :class:`~analysis_service.report.RepairedQuote`
    marks, rendered onto the draft each one names so the evidence step reads
    what the agent wrote beside the span the service put in its place. The
    first pass hands them in; the re-ask hands in none, because its job is
    structural and it is told not to re-decide a verdict.
    """
    shown = unsettled_drafts(drafts)
    duplicates = duplicate_groups(shown, system_model)
    rated_unlike = rating_disagreements(shown)
    chosen = shown if only is None else [d for d in shown if d.id in only]
    return _ruling_view(chosen, duplicates, rated_unlike, repaired)


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
    #: Every drafted ID a problem names, sorted: the whole of what the re-ask
    #: may change. :func:`merge_retry` holds it to that.
    repairable: list[str]


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
        repairable=sorted(problems.repairable),
    )
