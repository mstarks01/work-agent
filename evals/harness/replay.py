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

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from analysis_service.assertions import (
    ABSENT,
    GRAPH_BOUND,
    REGISTRY,
    UNKNOWN,
    Assertion,
    AssertionCatalog,
    assertion_id,
    identity_parts,
)
from analysis_service.system_model import DataFlow, SystemModel
from evals.harness.alignment import Alignment, element_type, slug_key
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
)

#: The fates a prompt change can aim at: the reference was not found, and the
#: instrument could say what happened to it.
LOSSES: frozenset[str] = frozenset(FATES) - {"found", "renamed"}

RowFate = Literal["found", "worded", "wrong_value", "rescoped", "omitted"]
ROW_FATES: tuple[RowFate, ...] = (
    "found",
    "worded",
    "wrong_value",
    "rescoped",
    "omitted",
)

#: The row fates a prompt change can aim at. ``worded`` is outside: two
#: spellings of one fact are a reader's question, and a target list that
#: carried them would name a row the model found.
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

    def to_json(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
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
    unpaired = tuple(alignment.unaligned_produced)
    flows = {flow.id: flow for flow in reference.data_flows}
    loose_flows = [flow for flow in produced.data_flows if flow.id in set(unpaired)]
    loose_nodes = [one for one in unpaired if element_type(one) != DataFlow.id_prefix]
    for one in alignment.unaligned_reference:
        if one in ambiguous:
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
    label = flow.id.rsplit(":", 1)[-1]
    touching = tuple(
        one.id
        for one in loose
        if one.id.rsplit(":", 1)[-1] == label
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


def signed_reference(corpus_dir: Path, case: GoldenCase) -> AssertionCatalog | None:
    """The case's reference catalog, or ``None`` where no signed file grades it."""
    if unsigned_rows(corpus_dir, case) != 0:
        return None
    sources = {source.label: source.text for source in case.sources}
    return reference_catalog(load_facts(corpus_dir / case.id), case.model, sources)


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
    case: GoldenCase, reference: AssertionCatalog, result: AssertionResult
) -> AssertionReplay:
    """Grade one archived proposal, re-resolved, against the signed reference."""
    available: dict[tuple[str, str], list[Assertion]] = defaultdict(list)
    for entry in result.catalog.entries:
        available[entry.subject, entry.predicate].append(entry)
    rows = []
    for ours in reference.entries:
        candidates = available[ours.subject, ours.predicate]
        if not candidates:
            rows.append(ReferenceRowFate(assertion_id(ours), "omitted"))
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


@dataclass(frozen=True)
class Arm:
    """What one archived sweep was: the node's instruction and the models that ran it."""

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


#: The node whose emission each mode archives.
NODE_OF: Mapping[str, str] = {"extraction": "extract", "assertions": "assert"}


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
    tiers = artifact.block("models")["tiers"]
    models = tuple(
        sorted({f"{tier['vendor']}/{tier['model']}" for tier in tiers.values()})
    )
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
    """
    counts: Counter[str] = Counter()
    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    per_element: dict[str, Counter[str]] = defaultdict(Counter)
    wrong_facts: Counter[str] = Counter()
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
