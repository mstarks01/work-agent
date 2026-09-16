"""Archived emissions re-scored under the coordinates that stand, and every loss named (#961).

An extraction sweep keeps what ``extract`` emitted and an assertion sweep
keeps what ``assert`` proposed, so a scorer, a normalizer or a reference that
moved after the sweep ran can be applied to the emissions already paid for.
This instrument does that over any number of archived sweeps and gives every
reference element one fate, so a prompt change has a target it can be aimed
at and a ceiling it cannot exceed before anything is spent.

## Extraction fates, one per blessed element

Read off :mod:`evals.harness.alignment`, never off names:

* ``found``: paired by exact ID.
* ``renamed``: paired on a reader's alias, or a flow paired under its own
  label or the discriminators between aligned endpoints.
* ``respelled``: unpaired, and an unpaired produced element of its type
  carries the same slug once each segment is singularised — ``shoppers``
  for ``shopper``. The strict figures charge it as a miss and an extra;
  which spelling ``extract.md`` asks for is naming conformity, not a
  component the model failed to read.
* ``mistyped``: unpaired, and an unpaired produced element carries the same
  slug under another type. A store written as a process.
* ``misattached``: an unpaired flow whose endpoints were both found, with no
  produced flow between them, and an unpaired produced flow under the same
  label touching one of them. The interaction exists and points elsewhere.
* ``endpoint_unaligned``: an unpaired flow with an endpoint nothing stands
  for. Charged to the endpoint, so a dropped node is one loss and not one
  plus every flow through it.
* ``omitted``: unpaired, with nothing that could stand for it — no unpaired
  produced element of its type, or for a flow between found endpoints, no
  unpaired produced flow between or touching them.
* ``ambiguous``: listed by the alignment as having two candidates.
* ``unresolved``: unpaired, with an unpaired produced element of its type
  beside it, or a flow between its found endpoints under another label. A
  rename nobody ruled on, or an omission beside an invention; this instrument
  cannot say which, and says so rather than guessing.

A **wrong fact** is not a fate. It is a scored attribute that differs on a
paired element, read off :attr:`~evals.harness.modes.ExtractionScore.differing`,
and one element can carry several. An **extra** is an unpaired produced
element, carried as a count: what it is waits on a ruling (#961 step 3).

## Assertion fates, one per signed reference row

Read off :func:`~analysis_service.assertions.identity_parts`, against the
produced rows on the same subject and predicate:

* ``found``: the same scope and the same canonical value.
* ``worded``: the same scope and the same state — both stated, both absent,
  both unknown — where the predicate's value is free text or names one of the
  layer's own subjects, so the two spellings are a reader's question.
* ``wrong_value``: the same scope and a value that disagrees: a different
  state, or a different term or graph-bound reference.
* ``rescoped``: the same subject and predicate at another scope.
* ``omitted``: no produced row on the subject and predicate.

A produced row no reference row took is ``misattached`` where a reference row
carries its predicate and value on another subject, and ``unreviewed``
otherwise: the reference lists what the sources state and not everything they
do not, so a row outside it is a candidate for a ruling and never an error by
inference. A row the resolver dropped is ``rejected``.

**A reference an agent drafted grades nothing.** A case whose facts file
carries an unsigned row is skipped by name, and the skip is printed.

## What a replay cannot see

A replay applies today's code to yesterday's emission. It cannot see a
behaviour the prompt under test would have changed — #938 predicted zero
``duplicate-ref`` from a replay and measured two — so its ceiling is a ceiling
and a run confirms.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

from analysis_service.assertions import (
    ABSENT,
    GRAPH_BOUND,
    REGISTRY,
    UNKNOWN,
    UNPROJECTED,
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    CatalogProposal,
    assertion_id,
    identity_parts,
    settled,
    snap_subject,
)
from analysis_service.grounding import normalize
from analysis_service.system_model import DataFlow, SystemModel, flow_label
from evals.harness.alignment import (
    Alignment,
    align,
    element_type,
    placeholder_zones,
    slug_key,
)
from evals.harness.artifact import EvalArtifact
from evals.harness.modes import (
    AssertionResult,
    AttributeCheck,
    ExtractionResult,
    score_extraction,
)
from evals.harness.reference import GoldenCase
from evals.reference_facts import facts_path, load_facts, reference_catalog

Fate = Literal[
    "found",
    "renamed",
    "respelled",
    "mistyped",
    "misattached",
    "endpoint_unaligned",
    "omitted",
    "ambiguous",
    "unresolved",
    "placeholder",
]
FATES: tuple[Fate, ...] = (
    "found",
    "renamed",
    "respelled",
    "mistyped",
    "misattached",
    "endpoint_unaligned",
    "omitted",
    "ambiguous",
    "unresolved",
    "placeholder",
)

#: The fates a prompt change can aim at: the reference was not found, and the
#: instrument could say what happened to it. ``placeholder`` is outside: a
#: zone the reference holds only because the schema requires one, which a
#: reader ruled a producer need not draw, so nothing stood for it and nothing
#: had to (:func:`~evals.harness.alignment.placeholder_zones`). A produced
#: zone that pairs with one still reads ``found`` or ``renamed``.
LOSSES: frozenset[str] = frozenset(FATES) - {"found", "renamed", "placeholder"}

RowFate = Literal["found", "worded", "wrong_value", "rescoped", "omitted", "silent"]
ROW_FATES: tuple[RowFate, ...] = (
    "found",
    "worded",
    "wrong_value",
    "rescoped",
    "omitted",
    "silent",
)

#: The row fates a prompt change can aim at. ``worded`` is outside: two
#: spellings of one fact are a reader's question, and a target list that
#: carried them would name a row the model found. ``silent`` is outside too:
#: the reference records a forced placement as an unknown the source never
#: raised, and the prompt tells the model to write no such row, so nobody
#: produced it and nobody should have. A produced value against one still
#: reads ``wrong_value``, which is the defect that matters.
ROW_LOSSES: frozenset[str] = frozenset({"wrong_value", "rescoped", "omitted"})

ProducedFate = Literal["matched", "misattached", "unreviewed"]
PRODUCED_FATES: tuple[ProducedFate, ...] = ("matched", "misattached", "unreviewed")


@dataclass(frozen=True)
class ElementFate:
    """One blessed element, what stood for it, and what could have."""

    reference: str
    fate: Fate
    #: The produced element paired with it, on a ``found`` or ``renamed``.
    produced: str = ""
    #: The produced elements this instrument could not rule out, on any fate
    #: that names some: the same slug under another type, the flows touching
    #: the endpoints, the unpaired elements of its type.
    candidates: tuple[str, ...] = ()
    #: What the pair rests on, on a ``found`` or ``renamed``: the alignment's
    #: own evidence kind, so a table can say how much of ``renamed`` a rule
    #: that reads structure carries. Empty on every loss.
    evidence: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "evidence": self.evidence,
            "fate": self.fate,
            "produced": self.produced,
            "candidates": list(self.candidates),
        }


@dataclass(frozen=True)
class ExtractionReplay:
    """One archived extraction, re-scored: every blessed element's fate."""

    case_id: str
    #: Whether the emission parsed to a model under today's gate. A refused
    #: emission has every element ``omitted``, which is the honest number and
    #: also one to read beside this flag.
    parsed: bool
    fates: tuple[ElementFate, ...]
    wrong_facts: tuple[AttributeCheck, ...]
    extra: tuple[str, ...]
    issues: tuple[str, ...] = ()

    @property
    def counts(self) -> Counter[str]:
        return Counter(row.fate for row in self.fates)

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "parsed": self.parsed,
            "counts": {fate: self.counts[fate] for fate in FATES},
            "wrong_facts": [check.to_json() for check in self.wrong_facts],
            "extra": list(self.extra),
            "issues": list(self.issues),
            "fates": [row.to_json() for row in self.fates],
        }


def replay_extraction(case: GoldenCase, result: ExtractionResult) -> ExtractionReplay:
    """Score one archived emission under today's scorer and name every loss."""
    score = score_extraction(case, result)
    return ExtractionReplay(
        case_id=case.id,
        parsed=result.extracted is not None,
        fates=classify_elements(case.model, result.extracted, score.alignment),
        wrong_facts=score.differing,
        extra=score.alignment.unaligned_produced,
        issues=tuple(f"{issue.code}: {issue.message}" for issue in result.issues),
    )


def classify_elements(
    reference: SystemModel, produced: SystemModel | None, alignment: Alignment
) -> tuple[ElementFate, ...]:
    """Every blessed element's fate, read off the alignment and the two models."""
    rows = [
        ElementFate(
            pair.reference,
            "found" if pair.evidence == "exact" else "renamed",
            pair.produced,
            evidence=pair.evidence,
        )
        for pair in alignment.pairs
    ]
    if produced is None:
        rows.extend(
            ElementFate(one, "omitted") for one in alignment.unaligned_reference
        )
        return tuple(rows)
    ambiguous = {
        one: entry.produced for entry in alignment.ambiguous for one in entry.reference
    }
    placeholders = placeholder_zones(reference)
    unpaired = tuple(alignment.unaligned_produced)
    flows = {flow.id: flow for flow in reference.data_flows}
    loose_flows = [flow for flow in produced.data_flows if flow.id in set(unpaired)]
    loose_nodes = [one for one in unpaired if element_type(one) != DataFlow.id_prefix]
    for one in alignment.unaligned_reference:
        if one in placeholders:
            rows.append(ElementFate(one, "placeholder"))
        elif one in ambiguous:
            rows.append(ElementFate(one, "ambiguous", candidates=ambiguous[one]))
        elif one in flows:
            rows.append(_flow_fate(flows[one], alignment.produced_of, loose_flows))
        else:
            rows.append(_node_fate(one, loose_nodes))
    return tuple(rows)


def _node_fate(reference: str, loose: Sequence[str]) -> ElementFate:
    """A node nothing stood for: the same slug elsewhere, a type-mate, or nothing."""
    slug = slug_key(reference).partition(":")[2]
    same_slug = [one for one in loose if slug_key(one).partition(":")[2] == slug]
    same_type = tuple(
        one for one in loose if element_type(one) == element_type(reference)
    )
    respelled = tuple(one for one in same_slug if one in same_type)
    if respelled:
        return ElementFate(reference, "respelled", candidates=respelled)
    mistyped = tuple(one for one in same_slug if one not in same_type)
    if mistyped:
        return ElementFate(reference, "mistyped", candidates=mistyped)
    if same_type:
        return ElementFate(reference, "unresolved", candidates=same_type)
    return ElementFate(reference, "omitted")


def _flow_fate(
    flow: DataFlow, produced_of: Mapping[str, str], loose: Sequence[DataFlow]
) -> ElementFate:
    """A flow nothing stood for, charged to its endpoints before its own absence."""
    source = produced_of.get(flow.source)
    destination = produced_of.get(flow.destination)
    if source is None or destination is None:
        lost = tuple(
            end
            for end, found in ((flow.source, source), (flow.destination, destination))
            if found is None
        )
        return ElementFate(flow.id, "endpoint_unaligned", candidates=lost)
    between = tuple(
        one.id
        for one in loose
        if (one.source, one.destination) == (source, destination)
    )
    if between:
        return ElementFate(flow.id, "unresolved", candidates=between)
    label = flow_label(flow.id)
    touching = tuple(
        one.id
        for one in loose
        if flow_label(one.id) == label
        and {one.source, one.destination} & {source, destination}
    )
    if touching:
        return ElementFate(flow.id, "misattached", candidates=touching)
    return ElementFate(flow.id, "omitted")


@dataclass(frozen=True)
class ReferenceRowFate:
    """One signed reference row and the produced row that answered it, if any."""

    reference: str
    fate: RowFate
    produced: str = ""
    #: The reference rules the fact inferred and the produced row calls it
    #: stated. A grant is never stated, a credential's presentation is
    #: implied by its issue (#961 step 3), and a row claiming otherwise
    #: overstates what its span carries.
    basis_overstated: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "fate": self.fate,
            "produced": self.produced,
            "basis_overstated": self.basis_overstated,
        }


@dataclass(frozen=True)
class AssertionReplay:
    """One archived proposal, re-resolved and graded against a signed reference."""

    case_id: str
    rows: tuple[ReferenceRowFate, ...]
    #: Produced rows no reference row took, by what is known about each.
    produced: Mapping[str, ProducedFate]
    rejected: int
    issues: tuple[str, ...] = ()

    @property
    def counts(self) -> Counter[str]:
        return Counter(row.fate for row in self.rows)

    @property
    def produced_counts(self) -> Counter[str]:
        return Counter(self.produced.values())

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "counts": {fate: self.counts[fate] for fate in ROW_FATES},
            "basis_overstated": sum(row.basis_overstated for row in self.rows),
            "produced_counts": {
                fate: self.produced_counts[fate] for fate in PRODUCED_FATES
            },
            "rejected": self.rejected,
            "issues": list(self.issues),
            "rows": [row.to_json() for row in self.rows],
            "produced": dict(self.produced),
        }


def unsigned_rows(corpus_dir: Path, case: GoldenCase) -> int | None:
    """How many reference rows nobody signed, or ``None`` where no facts file exists."""
    case_dir = corpus_dir / case.id
    if not facts_path(case_dir).exists():
        return None
    facts = load_facts(case_dir)
    return sum(row.reviewed_by is None for row in facts.rows)


@dataclass(frozen=True)
class AliasTarget:
    """What a signed alias rewrites to, and under which produced subjects.

    ``within`` bounds the rewrite of a *value* that names a subject alias,
    and of a scope qualifier under a qualifier alias: empty means under any
    produced subject. A produced row whose own subject is the alias is
    rewritten whatever its context, because the row is about the thing
    itself.
    """

    to: str
    within: frozenset[str] = frozenset()

    def applies_under(self, produced_subject: str) -> bool:
        return not self.within or produced_subject in self.within


@dataclass(frozen=True)
class SignedReference:
    """A case's signed reference catalog and the alias rulings signed beside it.

    ``subject_aliases`` maps an alias subject ID to the reference's own and
    the context it holds under, and ``qualifier_aliases`` maps a qualifier's
    kind and normalized alias spelling to the reference's value and its
    context. Only a signed alias is in either map: an unsigned one is a
    draft, and a draft rewrites nothing.
    """

    catalog: AssertionCatalog
    subject_aliases: Mapping[str, AliasTarget] = field(default_factory=dict)
    qualifier_aliases: Mapping[tuple[str, str], AliasTarget] = field(
        default_factory=dict
    )

    @property
    def entries(self) -> list[Assertion]:
        return self.catalog.entries

    @property
    def subjects(self) -> list[Any]:
        return self.catalog.subjects


def signed_reference(corpus_dir: Path, case: GoldenCase) -> SignedReference | None:
    """The case's signed reference, or ``None`` where no signed file grades it."""
    if unsigned_rows(corpus_dir, case) != 0:
        return None
    sources = {source.label: source.text for source in case.sources}
    facts = load_facts(corpus_dir / case.id)
    return SignedReference(
        catalog=reference_catalog(facts, case.model, sources),
        subject_aliases={
            alias_id: AliasTarget(ruling.subject, frozenset(ruling.within))
            for ruling in facts.aliases.subjects
            if ruling.reviewed_by is not None
            for alias_id in ruling.alias_ids
        },
        qualifier_aliases={
            (ruling.kind, normalize(name)): AliasTarget(
                ruling.value, frozenset(ruling.within)
            )
            for ruling in facts.aliases.qualifiers
            if ruling.reviewed_by is not None
            for name in ruling.names
        },
    )


def under_aliases(produced: Assertion, reference: SignedReference) -> Assertion:
    """The produced row in the reference's spellings, where a signed alias rules.

    The one place an alias is applied, before any comparison: the subject,
    a value that points at one of the layer's own subjects, and each scope
    qualifier are each rewritten to the reference's spelling where a signed
    ruling names the produced one. A value and a qualifier are rewritten
    only under the produced subjects the ruling holds for. Everything else
    is left as written.
    """
    predicate = REGISTRY[produced.predicate]
    refers_to_own = predicate.value == "reference" and not (
        predicate.refers_to & GRAPH_BOUND
    )
    own = reference.subject_aliases.get(produced.subject)
    subject = produced.subject if own is None else own.to
    value = produced.value
    target = reference.subject_aliases.get(value) if refers_to_own else None
    if target is not None and target.applies_under(subject):
        value = target.to
    scope = []
    for qualifier in produced.scope:
        ruled = reference.qualifier_aliases.get(
            (qualifier.kind, normalize(qualifier.value))
        )
        if ruled is not None and ruled.applies_under(subject):
            qualifier = qualifier.model_copy(update={"value": ruled.to})
        scope.append(qualifier)
    return produced.model_copy(
        update={"subject": subject, "value": value, "scope": scope}
    )


def _state(value: str) -> str:
    if value in (ABSENT, UNKNOWN):
        return value
    return "stated"


def _compares_exactly(predicate: str) -> bool:
    """Whether two spellings of a value are two values, for this predicate.

    A term is canonical. A reference to a graph-bound subject is an element
    ID, which the alignment already settled. Free text, and a reference to
    one of the layer's own subjects — a credential or a principal slugged
    from whatever the model wrote — are a reader's question.
    """
    registered = REGISTRY[predicate]
    if registered.value == "term":
        return True
    return registered.value == "reference" and bool(registered.refers_to & GRAPH_BOUND)


def _row_fate(reference: Assertion, candidate: Assertion) -> RowFate:
    """How one produced row on the reference's subject and predicate answers it."""
    ours, theirs = identity_parts(reference), identity_parts(candidate)
    if ours.scope != theirs.scope:
        return "rescoped"
    if ours.value == theirs.value:
        return "found"
    same_state = _state(reference.value) == _state(candidate.value) == "stated"
    if same_state and not _compares_exactly(reference.predicate):
        return "worded"
    return "wrong_value"


#: The fate a reference row takes when several produced rows sit on its
#: subject and predicate: the best answer available, in this order.
_PREFERRED: tuple[RowFate, ...] = ("found", "worded", "wrong_value", "rescoped")
_PREFERENCE: Mapping[RowFate, int] = {
    fate: rank for rank, fate in enumerate(_PREFERRED)
}


def replay_assertions(
    case: GoldenCase, reference: SignedReference, result: AssertionResult
) -> AssertionReplay:
    """Grade one archived proposal, re-resolved, against the signed reference.

    Every produced row is read under the reference's signed aliases first,
    so a principal the model named otherwise, a credential a value points at
    under another name, and a scope spelled another way are compared as the
    reviewer ruled they should be. The fates are then the plain matcher's.
    """
    available: dict[tuple[str, str], list[Assertion]] = defaultdict(list)
    for entry in result.catalog.entries:
        aliased = under_aliases(entry, reference)
        available[aliased.subject, aliased.predicate].append(aliased)
    rows = []
    for ours in reference.entries:
        candidates = available[ours.subject, ours.predicate]
        if not candidates:
            unasked = ours.value == UNKNOWN and ours.reason == "silent"
            rows.append(
                ReferenceRowFate(assertion_id(ours), "silent" if unasked else "omitted")
            )
            continue
        fate, theirs = min(
            ((_row_fate(ours, one), one) for one in candidates),
            key=lambda pair: _PREFERENCE[pair[0]],
        )
        candidates.remove(theirs)
        rows.append(
            ReferenceRowFate(
                assertion_id(ours),
                fate,
                assertion_id(theirs),
                basis_overstated=ours.basis == "inferred" and theirs.basis == "stated",
            )
        )
    elsewhere = {
        (parts.predicate, parts.value)
        for parts in map(identity_parts, reference.entries)
    }
    produced: dict[str, ProducedFate] = {}
    for leftovers in available.values():
        for one in leftovers:
            parts = identity_parts(one)
            produced[assertion_id(one)] = (
                "misattached"
                if (parts.predicate, parts.value) in elsewhere
                else "unreviewed"
            )
    for row in rows:
        if row.produced:
            produced[row.produced] = "matched"
    return AssertionReplay(
        case_id=case.id,
        rows=tuple(rows),
        produced=produced,
        rejected=len({issue.row for issue in result.issues if issue.row is not None}),
        issues=tuple(f"{issue.code}: {issue.message}" for issue in result.issues),
    )


# --- Binding: the same proposal against the graphs extraction produced ---------
#
# The assertion benchmark seeded the blessed model, so every proposal names the
# blessed element IDs and its rows resolve against a graph a production job
# never has. Finding 9 of #961 asks the evaluator to run against the captured
# extracted graphs as well, so a fact the node recorded and a binding the graph
# could not take are counted apart.

#: What one proposed row came to against an extracted graph. ``bound`` is a
#: graph-bound row the extracted graph took. ``renamed`` is one whose subject
#: it refused although the alignment pairs the blessed subject with a
#: produced element, so an alias ruling or an alignment-aware resolver would
#: bind it; ``omitted`` is one whose subject the graph holds no element for.
#: ``referent_renamed`` and ``referent_omitted`` are the same two facts about
#: a row's reference *value* — a zone a component sits in — with the subject
#: itself bound. ``own`` is a row on one of the layer's own subjects, which
#: no graph decides. ``refused`` is a row the blessed model refused too, or
#: one the extracted graph refused for a reason that is not a binding.
BindFate = Literal[
    "bound",
    "renamed",
    "omitted",
    "referent_renamed",
    "referent_omitted",
    "own",
    "refused",
]
BIND_FATES: tuple[BindFate, ...] = get_args(BindFate)

#: The binding losses, which the pooled table lists as targets.
BIND_LOSSES: frozenset[str] = frozenset(
    {"renamed", "omitted", "referent_renamed", "referent_omitted"}
)


@dataclass(frozen=True)
class RowBinding:
    """One proposed row and what one extracted graph made of it."""

    row: int
    subject: str
    predicate: str
    fate: BindFate
    #: The produced element the blessed subject or referent aligns to, on a
    #: ``renamed`` fate of either kind.
    aligned: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "subject": self.subject,
            "predicate": self.predicate,
            "fate": self.fate,
            "aligned": self.aligned,
        }


@dataclass(frozen=True)
class BindingReplay:
    """One archived proposal re-resolved against one archived extracted graph."""

    case_id: str
    graph: str
    #: Whether the extraction emission parsed to a model; an emission that did
    #: not binds nothing, and its rows are absent rather than all omitted.
    parsed: bool
    rows: tuple[RowBinding, ...]
    #: The rows the evidence catalog would offer over the extracted graph and
    #: this proposal, beside the same count over the blessed graph. The
    #: consumer's own figure: what a lane would have been able to cite.
    offered: int
    offered_blessed: int

    @property
    def counts(self) -> Counter[str]:
        return Counter(row.fate for row in self.rows)

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "graph": self.graph,
            "parsed": self.parsed,
            "counts": {fate: self.counts[fate] for fate in BIND_FATES},
            "offered": self.offered,
            "offered_blessed": self.offered_blessed,
            "rows": [row.to_json() for row in self.rows],
        }


def _offered(record: AssertionRecord) -> int:
    """How many rows the evidence catalog would offer a lane from this record."""
    return sum(row.predicate in UNPROJECTED for row in settled(record.catalog))


def bind_assertions(
    case: GoldenCase,
    proposal: AssertionResult,
    extraction: ExtractionResult,
    graph: str,
) -> BindingReplay:
    """Resolve one archived proposal against one archived extracted graph.

    ``graph`` names the extraction artifact the graph came from, so a pooled
    table can say which sweep's graphs refused a binding. Which binding
    failed is read off the resolver's own refusal code for the row: a
    ``dangling-subject`` is the subject, an ``illegal-value`` on a reference
    predicate is the referent, and either is charged to a rename the
    alignment pairs or to an element the graph holds nothing for.

    The proposal is resolved twice through the one reader production uses,
    once against the blessed model and once against the extracted one, and
    each row's fate is read off which of the two refused it. A row the
    blessed model refused is ``refused`` whatever the graph did. A row only
    the extracted graph refused is a binding failure, and the alignment says
    which kind: the blessed subject aligned to a produced element is a
    ``renamed`` binding an alias ruling would take, and one aligned to
    nothing is a subject the graph ``omitted``.
    """
    sources = {source.label: source.text for source in case.sources}
    proposed = CatalogProposal.model_validate(proposal.proposal)
    blessed = AssertionRecord.of(proposed, case.model, sources)
    if extraction.extracted is None:
        return BindingReplay(case.id, graph, False, (), 0, _offered(blessed))
    extracted = AssertionRecord.of(proposed, extraction.extracted, sources)
    refused_blessed = {issue.row for issue in blessed.issues if issue.row is not None}
    refused_extracted = {
        issue.row for issue in extracted.issues if issue.row is not None
    }
    produced_of = align(case, extraction.extracted).produced_of
    codes: dict[int, set[str]] = defaultdict(set)
    for issue in extracted.issues:
        if issue.row is not None:
            codes[issue.row].add(issue.code)
    rows = []
    for index, row in enumerate(proposed.assertions):
        fate: BindFate
        aligned = ""
        if index in refused_blessed:
            fate = "refused"
        elif index not in refused_extracted:
            fate = "bound" if row.subject_type in GRAPH_BOUND else "own"
        elif "dangling-subject" in codes[index]:
            blessed_id = snap_subject(row.subject_type, row.subject, case.model)
            aligned = produced_of.get(blessed_id or "", "")
            fate = "renamed" if aligned else "omitted"
        elif "illegal-value" in codes[index]:
            referent_type = next(iter(REGISTRY[row.predicate].refers_to))
            blessed_id = snap_subject(referent_type, row.value, case.model)
            aligned = produced_of.get(blessed_id or "", "")
            fate = "referent_renamed" if aligned else "referent_omitted"
        else:
            fate = "refused"
        rows.append(RowBinding(index, row.subject, row.predicate, fate, aligned))
    return BindingReplay(
        case_id=case.id,
        graph=graph,
        parsed=True,
        rows=tuple(rows),
        offered=_offered(extracted),
        offered_blessed=_offered(blessed),
    )


@dataclass(frozen=True)
class Arm:
    """What one archived sweep was: the node's instruction and the models that answered it.

    ``models`` is what answered the archived node, read off the sweep's own
    execution record, never the tier table. Two sweeps with one tier table
    and one prompt are two arms when the node ran on different tiers, which
    is exactly the per-phase model comparison #961 step 6 asks for.
    """

    node: str
    instruction: str
    models: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.node}@{self.instruction[:12]} on {', '.join(self.models)}"

    def to_json(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "instruction": self.instruction,
            "models": list(self.models),
        }


#: The node whose emission each mode archives. An end-to-end sweep keeps the
#: first pass of the same node the extraction mode does, so it replays under
#: the same fates; its repair's emission is archived and not graded here.
NODE_OF: Mapping[str, str] = {
    "extraction": "extract",
    "end-to-end": "extract",
    "assertions": "assert",
}


def arm_of(artifact: EvalArtifact) -> Arm:
    """Which prompt and which models an archived sweep ran, off its own record."""
    node = NODE_OF[artifact.mode]
    digests = {
        row["sha256"] for row in artifact.block("instruction") if row["node"] == node
    }
    if len(digests) != 1:
        raise ValueError(
            f"{artifact.path}: the instruction block names {len(digests)} digests"
            f" for {node}, so the sweep cannot be placed on one arm"
        )
    executions = artifact.block("provenance")["node_runs"].get(node, [])
    if not executions:
        raise ValueError(
            f"{artifact.path}: the provenance block records no execution of {node},"
            " so the sweep cannot be placed on an arm"
        )
    models = tuple(sorted({execution["requested_model"] for execution in executions}))
    return Arm(node, next(iter(digests)), models)


@dataclass(frozen=True)
class SweepReplay:
    """One archived sweep under today's coordinates."""

    artifact: str
    arm: Arm
    commit: str
    #: ``None`` where the sweep predates the tree state being recorded.
    clean: bool | None
    corpus_digest: str
    extractions: tuple[ExtractionReplay, ...] = ()
    assertions: tuple[AssertionReplay, ...] = ()
    skipped: Mapping[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "arm": self.arm.to_json(),
            "repo_commit": {"commit": self.commit, "clean": self.clean},
            "corpus_digest": self.corpus_digest,
            "extractions": [row.to_json() for row in self.extractions],
            "assertions": [row.to_json() for row in self.assertions],
            "skipped": dict(self.skipped),
        }


def pooled_extraction(sweeps: Sequence[SweepReplay]) -> dict[str, Any]:
    """Every extraction fate over a set of sweeps, and the elements that lost most.

    ``targets`` is what a prompt change aims at: each blessed element by the
    number of emissions in which it took a loss, with the losses it took. The
    count is the change's ceiling on that element, and the sum over a fate is
    its ceiling on the fate.

    ``spread`` is what the ceiling is read against: the standard deviation of
    each fate's per-sweep count, over the sweeps that ran every case the arm
    holds, so a sweep the spend hold stopped short does not read as a loss.
    A ceiling inside the spread gets no run (``evals/TUNING.md`` step 3).
    ``None`` where fewer than two such sweeps exist.
    """
    counts: Counter[str] = Counter()
    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    per_element: dict[str, Counter[str]] = defaultdict(Counter)
    wrong_facts: Counter[str] = Counter()
    renamed_by: Counter[str] = Counter()
    emissions = 0
    unparsed = 0
    extra = 0
    for sweep in sweeps:
        for replay in sweep.extractions:
            emissions += 1
            unparsed += not replay.parsed
            extra += len(replay.extra)
            counts.update(replay.counts)
            for row in replay.fates:
                by_type[element_type(row.reference)][row.fate] += 1
                if row.fate == "renamed":
                    renamed_by[row.evidence] += 1
                if row.fate in LOSSES:
                    per_element[f"{replay.case_id}/{row.reference}"][row.fate] += 1
            wrong_facts.update(check.key for check in replay.wrong_facts)
    targets = sorted(
        per_element.items(), key=lambda item: (-sum(item[1].values()), item[0])
    )
    return {
        "sweeps": len(sweeps),
        "emissions": emissions,
        "unparsed": unparsed,
        "extra": extra,
        "fates": {fate: counts[fate] for fate in FATES},
        # What each renamed pair rests on, by the alignment's evidence kind,
        # so a rule that reads structure is visible in the number it moves.
        "renamed_by": dict(sorted(renamed_by.items())),
        "spread": _spread(sweeps),
        # The same fates per element type, because the question a prompt
        # change asks is usually about one type: whether the actors were
        # dropped (#295), or the zones were invented.
        "by_type": {
            kind: {fate: fates[fate] for fate in FATES}
            for kind, fates in sorted(by_type.items())
        },
        "wrong_facts": dict(sorted(wrong_facts.items())),
        "targets": [
            {"element": element, "losses": sum(fates.values()), "by_fate": dict(fates)}
            for element, fates in targets
        ],
    }


def _spread(sweeps: Sequence[SweepReplay]) -> dict[str, Any]:
    """Each fate's per-sweep standard deviation, whole and by element type."""
    held = {replay.case_id for sweep in sweeps for replay in sweep.extractions}
    full = [
        sweep
        for sweep in sweeps
        if {replay.case_id for replay in sweep.extractions} == held
    ]
    totals: list[Counter[str]] = []
    by_type: list[dict[str, Counter[str]]] = []
    for sweep in full:
        total: Counter[str] = Counter()
        kinds: dict[str, Counter[str]] = defaultdict(Counter)
        for replay in sweep.extractions:
            for row in replay.fates:
                total[row.fate] += 1
                kinds[element_type(row.reference)][row.fate] += 1
        totals.append(total)
        by_type.append(kinds)

    def sd(values: Sequence[int]) -> float | None:
        return round(statistics.stdev(values), 2) if len(values) > 1 else None

    kinds_seen = sorted({kind for kinds in by_type for kind in kinds})
    return {
        "sweeps": len(full),
        "sd": {fate: sd([total[fate] for total in totals]) for fate in FATES},
        "by_type": {
            kind: {fate: sd([kinds[kind][fate] for kinds in by_type]) for fate in FATES}
            for kind in kinds_seen
        },
    }


def pooled_assertions(sweeps: Sequence[SweepReplay]) -> dict[str, Any]:
    """Every assertion fate over a set of sweeps, and the rows omitted most."""
    counts: Counter[str] = Counter()
    produced: Counter[str] = Counter()
    per_row: dict[str, Counter[str]] = defaultdict(Counter)
    overstated = 0
    rejected = 0
    emissions = 0
    for sweep in sweeps:
        for replay in sweep.assertions:
            emissions += 1
            rejected += replay.rejected
            counts.update(replay.counts)
            produced.update(replay.produced_counts)
            overstated += sum(row.basis_overstated for row in replay.rows)
            for row in replay.rows:
                if row.fate in ROW_LOSSES:
                    per_row[f"{replay.case_id}/{row.reference}"][row.fate] += 1
    return {
        "sweeps": len(sweeps),
        "emissions": emissions,
        "fates": {fate: counts[fate] for fate in ROW_FATES},
        "basis_overstated": overstated,
        "produced": {fate: produced[fate] for fate in PRODUCED_FATES},
        "rejected": rejected,
        "targets": [
            {"row": row, "losses": sum(fates.values()), "by_fate": dict(fates)}
            for row, fates in sorted(
                per_row.items(), key=lambda item: (-sum(item[1].values()), item[0])
            )
        ],
    }


@dataclass(frozen=True)
class BindingSweep:
    """One archived assertion sweep bound to every archived extracted graph given."""

    artifact: str
    arm: Arm
    bindings: tuple[BindingReplay, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "arm": self.arm.to_json(),
            "bindings": [binding.to_json() for binding in self.bindings],
        }


def pooled_bindings(sweeps: Sequence[BindingSweep]) -> dict[str, Any]:
    """Every binding fate over a set of sweeps, and the rows refused most."""
    counts: Counter[str] = Counter()
    per_row: dict[str, Counter[str]] = defaultdict(Counter)
    pairs = unparsed = offered = offered_blessed = 0
    for sweep in sweeps:
        for binding in sweep.bindings:
            pairs += 1
            if not binding.parsed:
                unparsed += 1
                continue
            counts.update(binding.counts)
            offered += binding.offered
            offered_blessed += binding.offered_blessed
            for row in binding.rows:
                if row.fate in BIND_LOSSES:
                    key = f"{binding.case_id}/{row.subject}/{row.predicate}"
                    per_row[key][row.fate] += 1
    graph_bound = sum(counts[fate] for fate in ("bound", "renamed", "omitted"))
    return {
        "sweeps": len(sweeps),
        "pairs": pairs,
        "unparsed": unparsed,
        "fates": {fate: counts[fate] for fate in BIND_FATES},
        "bound_share": counts["bound"] / graph_bound if graph_bound else None,
        "offered": offered,
        "offered_blessed": offered_blessed,
        "targets": [
            {"row": row, "losses": sum(fates.values()), "by_fate": dict(fates)}
            for row, fates in sorted(
                per_row.items(), key=lambda item: (-sum(item[1].values()), item[0])
            )
        ],
    }


def render_bindings(sweeps: Sequence[BindingSweep], targets: int = 10) -> None:
    """The pooled binding table, one per proposal arm."""
    grouped: dict[Arm, list[BindingSweep]] = defaultdict(list)
    for sweep in sweeps:
        grouped[sweep.arm].append(sweep)
    for arm, arm_sweeps in grouped.items():
        pool = pooled_bindings(arm_sweeps)
        print(f"\n== {arm.label}: {len(arm_sweeps)} proposal sweep(s)")
        print(
            f"  {pool['pairs']} proposal/graph pair(s), {pool['unparsed']} graph(s)"
            " refused by today's gate"
        )
        print("  row fate             total")
        for fate in BIND_FATES:
            print(f"  {fate:<20} {pool['fates'][fate]:>5}")
        share = pool["bound_share"]
        print(
            f"  bound share of graph-bound rows: {'n/a' if share is None else f'{share:.3f}'}"
        )
        print(
            f"  rows a lane could cite: {pool['offered']} over the extracted graphs,"
            f" {pool['offered_blessed']} over the blessed one"
        )
        if pool["targets"]:
            print(f"  targets (top {targets} of {len(pool['targets'])}):")
            for target in pool["targets"][:targets]:
                fates = ", ".join(
                    f"{fate} {n}" for fate, n in sorted(target["by_fate"].items())
                )
                print(f"    {target['losses']:>3}  {target['row']}  ({fates})")


def binding_artifact(
    sweeps: Sequence[BindingSweep], commit: str, clean: bool | None, corpus_digest: str
) -> dict[str, Any]:
    """The whole binding replay, with the coordinates it was scored under named first."""
    grouped: dict[Arm, list[BindingSweep]] = defaultdict(list)
    for sweep in sweeps:
        grouped[sweep.arm].append(sweep)
    return {
        "coordinates": {
            "repo_commit": {"commit": commit, "clean": clean},
            "corpus_digest": corpus_digest,
        },
        "arms": [
            {"arm": arm.to_json(), "bindings": pooled_bindings(arm_sweeps)}
            for arm, arm_sweeps in grouped.items()
        ],
        "bindings": [sweep.to_json() for sweep in sweeps],
    }


def by_arm(sweeps: Iterable[SweepReplay]) -> dict[Arm, list[SweepReplay]]:
    grouped: dict[Arm, list[SweepReplay]] = defaultdict(list)
    for sweep in sweeps:
        grouped[sweep.arm].append(sweep)
    return dict(grouped)


#: How a sweep's tree state prints beside its commit, by what was recorded.
TREE_STATE: Mapping[bool | None, str] = {
    True: "",
    False: " (dirty tree)",
    None: " (tree state unrecorded)",
}


def render(sweeps: Sequence[SweepReplay], targets: int = 10) -> None:
    """The pooled tables, one per arm, with the elements a change would aim at."""
    for arm, arm_sweeps in by_arm(sweeps).items():
        print(f"\n== {arm.label}: {len(arm_sweeps)} sweep(s)")
        for sweep in arm_sweeps:
            state = TREE_STATE[sweep.clean]
            print(f"   {sweep.artifact} @ {sweep.commit[:9]}{state}")
            for case_id, reason in sweep.skipped.items():
                print(f"     skipped {case_id}: {reason}")
        if any(sweep.extractions for sweep in arm_sweeps):
            _render_extraction(pooled_extraction(arm_sweeps), targets)
        if any(sweep.assertions for sweep in arm_sweeps):
            _render_assertions(pooled_assertions(arm_sweeps), targets)


def _render_extraction(pool: dict[str, Any], targets: int) -> None:
    per = pool["sweeps"] or 1
    print(
        f"  {pool['emissions']} emission(s), {pool['unparsed']} refused by today's gate,"
        f" {pool['extra']} extra element(s) unreviewed"
    )
    kinds = list(pool["by_type"])
    heading = " ".join(f"{kind:>8}" for kind in kinds)
    print(f"  {'fate':<20} {'total':>5} {'per sweep':>9}  {heading}")
    for fate in FATES:
        per_kind = " ".join(f"{pool['by_type'][kind][fate]:>8}" for kind in kinds)
        total, mean = pool["fates"][fate], pool["fates"][fate] / per
        print(f"  {fate:<20} {total:>5} {mean:>9.1f}  {per_kind}")
    if pool["renamed_by"]:
        listed = ", ".join(f"{kind} {n}" for kind, n in pool["renamed_by"].items())
        print(f"  renamed by: {listed}")
    spread = pool["spread"]
    print(
        f"  spread: sd of the per-sweep count over the {spread['sweeps']} sweep(s)"
        " that ran every case; a ceiling inside it gets no run"
    )
    for fate in FATES:
        per_kind = " ".join(
            f"{_sd(spread['by_type'].get(kind, {}).get(fate)):>8}" for kind in kinds
        )
        print(f"  {fate:<20} {'':>5} {_sd(spread['sd'][fate]):>9}  {per_kind}")
    if pool["wrong_facts"]:
        listed = ", ".join(
            f"{key} {count}" for key, count in pool["wrong_facts"].items()
        )
        print(f"  wrong facts on paired elements: {listed}")
    if pool["targets"]:
        print(f"  targets (top {targets} of {len(pool['targets'])}):")
        for target in pool["targets"][:targets]:
            fates = ", ".join(
                f"{fate} {n}" for fate, n in sorted(target["by_fate"].items())
            )
            print(f"    {target['losses']:>3}  {target['element']}  ({fates})")


def _sd(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _render_assertions(pool: dict[str, Any], targets: int) -> None:
    print(f"  {pool['emissions']} proposal(s), {pool['rejected']} row(s) rejected")
    print("  reference row fate   total")
    for fate in ROW_FATES:
        print(f"  {fate:<20} {pool['fates'][fate]:>5}")
    print(f"  basis overstated     {pool['basis_overstated']:>5}")
    produced = ", ".join(f"{fate} {pool['produced'][fate]}" for fate in PRODUCED_FATES)
    print(f"  produced rows: {produced}")
    if pool["targets"]:
        print(f"  targets (top {targets} of {len(pool['targets'])}):")
        for target in pool["targets"][:targets]:
            fates = ", ".join(
                f"{fate} {n}" for fate, n in sorted(target["by_fate"].items())
            )
            print(f"    {target['losses']:>3}  {target['row']}  ({fates})")


def artifact(
    sweeps: Sequence[SweepReplay], commit: str, clean: bool | None, corpus_digest: str
) -> dict[str, Any]:
    """The whole replay, with the coordinates it was scored under named first."""
    arms = by_arm(sweeps)
    return {
        "coordinates": {
            "repo_commit": {"commit": commit, "clean": clean},
            "corpus_digest": corpus_digest,
        },
        "arms": [
            {
                "arm": arm.to_json(),
                "extraction": pooled_extraction(arm_sweeps)
                if any(sweep.extractions for sweep in arm_sweeps)
                else None,
                "assertions": pooled_assertions(arm_sweeps)
                if any(sweep.assertions for sweep in arm_sweeps)
                else None,
            }
            for arm, arm_sweeps in arms.items()
        ],
        "sweeps": [sweep.to_json() for sweep in sweeps],
    }
