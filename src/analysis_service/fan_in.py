"""One framework's lane agents' proposals, merged into the drafts its critic reads.

The fan-in is the first point at which all of one framework's lanes' proposals,
the **Valid System Model** and the job's sources exist together, and every
mechanical check that needs the whole set runs here. :func:`fan_in` is the one
interface. It takes the lane batches as the agents emitted them and returns the
drafts that survived, the marks the service recorded about them, the units the
package deferred, and the per-lane coverage. The graph's ``merge`` node reads
state, calls it, parks what it returns and routes; nothing else is decided
there.

Behind the interface, in order: a proposal naming an identifier its framework
does not have is marked; a proposal the package defers never becomes a draft;
each lane's proposals resolve their evidence references against the catalog
derived from the same model the agents chose from; :func:`join_drafts` runs the
whole-set checks, snapping references, dropping duplicate IDs, resolving and
bounding element references, verifying quotes and dropping settled duplicates;
a draft on a unit the package's own rules ruled out is refused; and every mark
is narrowed once to the drafts that survived. Each pass marks what it sees and
cannot know what a later pass drops, so the narrowing is the last step and
happens nowhere else.

**One package per call.** Two frameworks' drafts never meet here: they are
ruled by different critics against different questions, and a duplicate across
them is not a duplicate. The graph runs this once per selected framework.

Every check fails closed or marks, and the split is the whole policy of this
seam. A check that decides whether a finding *means* anything fails closed
(:class:`DraftJoinError`); a check that describes how complete it is records a
mark for a reader. The fan-in has no re-ask path, so the second kind never
costs a report, and ADR 0019 rules that a fault in one entry of one claim never
costs the job.

Model output is untrusted input (OWASP LLM05), and the service validates it
here before anything reaches the critic or the report.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import NamedTuple

from analysis_service.candidates import generate_candidates
from analysis_service.coverage import build_coverage
from analysis_service.critic import endpoint_targets
from analysis_service.evidence import (
    evidence_catalog,
    ground_issues,
    invalid_proposal_marks,
    known_proposals,
    resolve_proposals,
)
from analysis_service.frameworks import FrameworkPackage
from analysis_service.grounding import (
    PreparedSource,
    deadline_spent,
    meaning_moved,
    normalize,
    prepare_source,
    repair_deadline,
    repair_prepared,
    verify_normalized,
)
from analysis_service.references import canonical, snap
from analysis_service.report import (
    BEYOND_GROUNDS,
    ELEMENT_REF_MAX_CHARS,
    MENTION_MAX_CHARS,
    AnalysisMarks,
    Claim,
    DroppedClaim,
    Ground,
    LaneCoverage,
    ProposalBatch,
    RepairedQuote,
    UnresolvedMention,
    UnresolvedReference,
    UnverifiedGround,
)
from analysis_service.sources import CARRIED_EVIDENCE_KINDS
from analysis_service.system_model import ModelIndex, SystemModel, mentioned_ids

logger = logging.getLogger(__name__)

_NO_TEXTS: Mapping[str, str] = MappingProxyType({})


class FanIn(NamedTuple):
    """What one framework's fan-in produced, as one value.

    ``drafts`` are the agents' own, as the critic reads them. ``marks`` are the
    service's record of what the drafts failed to make good on, already
    narrowed to ``drafts``. ``deferred`` is each unit the package routed away
    from the critic against the reason. ``coverage`` is computed over the
    resolved drafts before the join, because the question it answers — did
    this lane look at the system — is about what the agents did rather than
    about what survived review.
    """

    drafts: list[Claim]
    marks: AnalysisMarks
    deferred: dict[str, str]
    coverage: list[LaneCoverage]


def fan_in(
    batches: Mapping[str, ProposalBatch],
    package: FrameworkPackage,
    model: SystemModel,
    sources: Mapping[str, str] = _NO_TEXTS,
    ruled_out: Mapping[str, str] = _NO_TEXTS,
) -> FanIn:
    """Merge one framework's lane batches into the drafts its critic sees.

    ``batches`` is each lane against the batch its agent emitted, validated as
    the package's own :class:`~analysis_service.report.ProposalBatch` type. A
    lane with no entry contributes nothing; whether an absent lane is a failed
    job is the graph's question, answered before this is called.

    The catalog is derived here from the same validated model the prepare step
    derived it from, rather than parked in state and read back — it is a pure
    function of that model, so deriving it twice is what guarantees the set an
    agent chose from and the set its choice is resolved against are the same
    set. Sharing one catalog would have to carry it through the session, where
    a value a node can reach is a value a node can change, and what it would
    buy is 0.048 ms on a corpus-sized model and 3.7 ms on one of 600 elements.
    ``evals/bench/deterministic.py catalog`` re-derives the two figures.

    ``sources`` maps each source's label to its text. Empty means the quote
    check does not run: the in-process engine drives a hand-authored model with
    no job behind it, and inventing a set would fail it on a citation that is
    not wrong. ``ruled_out`` is each unit the package's own rules settled at
    prepare against the reason; a draft on one is refused here whatever the
    agent read in its scope line, because a claim on it would put the same
    unit on the report twice, once as a claim and once as not applicable
    (#443).
    """
    catalog = evidence_catalog(model)
    invalid = {
        lane: invalid_proposal_marks(batch.invalid, package, lane)
        for lane, batch in batches.items()
    }
    # Identity before the split, so a proposal naming a requirement the
    # framework does not have is marked whichever side of the split it would
    # have taken (#659). The two marks per lane are one value from here on.
    known: dict[str, list] = {}
    for lane, batch in batches.items():
        known[lane], unknown = known_proposals(batch.claims, package, lane)
        invalid[lane] = invalid[lane].merged_with(unknown)
    # Split before resolving, because a deferred proposal must not become a
    # draft: the whole point is that it never reaches the critic. The package
    # decides which, since the unit and the reason are both its own.
    partitions = {
        lane: package.record.partition_proposals(
            known[lane], lane, CARRIED_EVIDENCE_KINDS
        )
        for lane in batches
    }
    deferred: dict[str, str] = {}
    for _, reasons in partitions.values():
        deferred.update(reasons)
    # Logged because the answer a proposal carries to make this decision does
    # not survive into a draft: without this line a run that defers nothing
    # leaves no trace of whether the agents ruled or the seam never ran.
    for lane, (kept, reasons) in partitions.items():
        logger.info(
            "%s/%s: %d proposals, %d kept, deferred for %s",
            package.name,
            lane,
            len(kept) + len(reasons),
            len(kept),
            sorted(Counter(reasons.values()).items()),
        )
    resolutions = {
        lane: resolve_proposals(kept, catalog, package, lane, model)
        for lane, (kept, _) in partitions.items()
    }
    drafts_by_lane = {
        lane: resolution.drafts for lane, resolution in resolutions.items()
    }
    joined = join_drafts(drafts_by_lane, package, model, sources)
    refused = [
        DroppedClaim.of(
            claim_id=draft.id,
            title=draft.title,
            reason=ruled_out[unit],
        )
        for draft in joined.drafts
        if (unit := package.record.unit_of(draft)) in ruled_out
    ]
    refused_ids = {dropped.claim_id for dropped in refused}
    merged = [draft for draft in joined.drafts if draft.id not in refused_ids]
    # Every mark this framework's fan-in produced, from all of its producers:
    # the join across its lanes, each lane's own evidence resolution, and the
    # refusals above. One value, because they share an owner, a standing and a
    # policy.
    marks = joined.marks.merged_with(AnalysisMarks(dropped_claims=refused))
    for lane, resolution in resolutions.items():
        marks = marks.merged_with(invalid[lane]).merged_with(resolution.marks)
    # Narrowed to the drafts that survived every pass above, once, after the
    # last producer. The block refuses a mark on a claim it does not carry.
    marks = marks.on_claims({draft.id for draft in merged})
    # Logged rather than reported, and the package decides what is worth saying:
    # a STRIDE lane that numbered its drafts 01, 02, 05 broke nothing a reader
    # could act on, and the agents are who it is about.
    for message in package.record.lane_diagnostics(merged):
        logger.warning(message)
    # The candidates are regenerated rather than read back from state: they are
    # a pure function of the same validated model and the same package rules,
    # so the two derivations cannot disagree.
    coverage = build_coverage(
        drafts_by_lane,
        generate_candidates(model, package.lanes, package.rules),
        model,
        package,
    )
    return FanIn(merged, marks, deferred, coverage)


class DraftJoinError(ValueError):
    """One framework's merged lane agents' drafts fail a mechanical check."""


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
                DroppedClaim.of(
                    claim_id=claim.id,
                    title=claim.title,
                    reason=(
                        "names only elements the system model does not contain"
                        f" ({', '.join(repr(ref) for ref in lost)})"
                    ),
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
                DroppedClaim.of(
                    claim_id=claim.id,
                    title=claim.title,
                    reason=(
                        "names only elements its grounds do not reach"
                        f" ({', '.join(repr(ref) for ref in lost)})"
                    ),
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
                DroppedClaim.of(
                    claim_id=claim.id,
                    title=claim.title,
                    reason="repeats the ID of an earlier draft in this framework",
                )
            )
            continue
        seen.add(claim.id)
        kept.append(claim)
    return kept, dropped


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
            grounds[index] = ground.model_copy(update={"text": repair.span})
            repairs.append(
                RepairedQuote(
                    claim_id=claim.id,
                    index=index,
                    written=ground.text,
                    similarity=round(repair.similarity, 3),
                    moved=list(meaning_moved(ground.text, repair.span)),
                    scan_complete=repair.complete,
                )
            )
        if len(unverified) == len(grounds):
            lost = "; ".join(
                f"{grounds[index].text!r} not found in {grounds[index].source_label!r}"
                for index in unverified
            )
            groundless.append(
                DroppedClaim.of(
                    claim_id=claim.id,
                    title=claim.title,
                    reason=f"no ground verifies: {lost}",
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
    issues = ground_issues(referenced.drafts, system_model)
    if issues:
        raise DraftJoinError("; ".join(issues))
    bounded = _bound_element_references(referenced.drafts, index)
    checked = (
        _verify_quotes(bounded.drafts, sources)
        if sources
        else _QuoteCheck(list(bounded.drafts), [], [], [])
    )
    kept, settled_duplicates = _drop_settled_duplicates(checked.drafts, index)
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
                *settled_duplicates,
            ],
        ).merged_with(package.record.claim_marks(kept)),
    )


def _drop_settled_duplicates(
    claims: Sequence[Claim], index: ModelIndex
) -> tuple[list[Claim], list[DroppedClaim]]:
    """Keep one of each set of drafts whose grounds settle them alike.

    **The one duplicate check no reader would otherwise make.** A draft its own
    grounds settle is ruled in code and never shown to a critic (#439), and
    :func:`~analysis_service.critic.critic_view` computes :func:`~analysis_service.critic.duplicate_groups` over the shown set —
    so two conditional drafts naming one action at one place both reach the
    report, and nothing anywhere compares them. The critic cannot: it is not
    given them.

    Runs last in the fan-in, on the drafts that survived every other check, so
    the targets it compares are the ones the report will carry rather than the
    ones an agent wrote. The key is :func:`~analysis_service.critic.duplicate_groups`'s own — the verb
    and the endpoint-resolved targets — because these are the same duplicates,
    found at a different seam.

    **First wins, and the choice is deterministic rather than good.** Lane order
    is the package's own, so two runs of one input drop the same copy. Picking
    the better-written of the two would be a judgement, and judgement is the
    critic's; what code can do here is stop one finding being reported twice.

    A draft carrying no verb belongs to a package whose identity is a catalog
    identifier, and its duplicates are ID collisions :func:`_drop_duplicate_ids`
    already refused. Those pass through untouched.
    """
    flows = index.flow_endpoints
    seen: dict[tuple[str, frozenset[str]], str] = {}
    kept: list[Claim] = []
    dropped: list[DroppedClaim] = []
    for claim in claims:
        settled = type(claim).settled_by_grounds(claim) is not None
        if not settled or claim.verb is None:
            kept.append(claim)
            continue
        key = (claim.verb, endpoint_targets(claim.affected_element_ids, flows))
        first = seen.get(key)
        if first is None:
            seen[key] = claim.id
            kept.append(claim)
            continue
        dropped.append(
            DroppedClaim.of(
                claim_id=claim.id,
                title=claim.title,
                reason=(
                    f"names the same action at the same place as {first!r}, and"
                    " both rest on an unstated control, so no critic sees either"
                    " to rule on the pair"
                ),
            )
        )
    return kept, dropped
