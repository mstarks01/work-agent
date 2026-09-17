"""A perfect reading, put through the deterministic path, and every loss charged.

#1003's comparison measures two things at once: how much of a source a model
reads, and how much of what it reads the code between the model and the catalog
keeps. A recall figure cannot separate them, so a route that reads well and a
route that binds well score alike.

This instrument separates them. It builds a **Source Fact Bundle** from the
signed reference itself — every fact a case's reviewer ruled, in local handles,
under quotes that locate in the sources — and puts it through the real
:func:`~analysis_service.factbundle.resolve_bundle`, the gate every arm shares
and the scorer every arm is graded by. The bundle stands in for a model that
read everything and got it right.

**It is oracle-assisted, and it is never an arm.** It reads the answers. Its
number is a *ceiling*, not a score: what it loses, no route can keep, and what
it keeps says nothing about what a model would emit.
``tests/test_evals_oracle.py`` holds it out of :data:`~evals.harness.arms.ARMS`
for that reason.

## What a stage means

Every required row is charged to the first stage that did not carry it, by
:data:`STAGES`:

* ``found``: the row reached the catalog under the reference's own identity.
* ``not-authored``: the bundle never carried it. The oracle could not cite the
  subject, or the schema cannot express the row. **This is the oracle's limit
  and not a defect in the code under test**, so it is reported apart.
* ``resolution``: the bundle carried it and
  :func:`~analysis_service.factbundle.resolve_bundle` did not keep it. The
  disposition's code says why.
* ``gate``: the resolver kept it and the assertion gate refused it.
* ``scored``: it reached the catalog and the matcher did not call it ``found``.

The three that follow ``not-authored`` are the deterministic path's own losses.
A run where all three are zero says the path keeps a perfect reading whole.

## What the oracle cannot author

Two shapes, and each one is a finding rather than a nuisance.

A **component whose zone no source states** cannot be placed. The graph
requires ``trust_zone``, so the oracle asserts the placement the blessed model
holds and marks it ``inferred``. The reference records the same component's zone
as ``unknown``, so the matcher charges the assertion as a wrong claim — which is
right, and is the cost the schema imposes on every route.

A **row whose value is the unknown sentinel on a graph-bound predicate** cannot
be written at all: the resolver reads the value as a zone handle and refuses it
as ``dangling-value``. Those rows sit outside the primary denominator, and
:attr:`CaseCharge.inexpressible` counts them.

## What this instrument cannot see

**A ceiling is insensitive to anything a perfect reading does not do.** The
bundle names the reference's own subjects, values and scopes, so the matcher
answers every row exactly; a rule that decides near misses — which spelling is
a rewording, which produced row a reference row takes, whether two rows claim a
value with the same force — is never reached. The bundle proposes no correction,
so the patch applicator does not run. Every span it cites locates, so the
placement rules refuse nothing.

Those rules are held by the tests beside them, and a green number here says
nothing about any of them. What it does say is that the path from a bundle to a
scored catalog carries a right answer whole, so a row a route loses is a row it
did not read.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, get_args

from analysis_service.assertions import (
    ABSENT,
    UNKNOWN,
    Assertion,
    ambiguous_quote,
    assertion_id,
    span_source,
    spans_for,
)
from analysis_service.factbundle import (
    GRAPH_REFERENTS,
    FactProposal,
    InteractionProposal,
    MentionProposal,
    SourceFactBundle,
    resolve_bundle,
)
from analysis_service.system_model import (
    DataStore,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
)
from evals.harness.arms import required_rows
from evals.harness.modes import AssertionResult
from evals.harness.reference import GoldenCase, load_corpus
from evals.harness.replay import SignedReference, replay_assertions, signed_reference

#: Which role builds each blessed element, by the class the model holds and the
#: ``kind`` the role fixes. A **table** rather than a branch: a class or a kind
#: added to the model raises here rather than reading as an element the oracle
#: quietly skips.
ROLE_FOR: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        (ExternalEntity.__name__, "human"): "person",
        (ExternalEntity.__name__, "external-system"): "external-system",
        (Process.__name__, ""): "process",
        (DataStore.__name__, ""): "store",
        (TrustBoundary.__name__, "network"): "network-zone",
        (TrustBoundary.__name__, "privilege"): "privilege-zone",
        (TrustBoundary.__name__, "tenant"): "tenant-zone",
        (TrustBoundary.__name__, "other"): "other-zone",
    }
)

#: The assertion layer's own subjects, which a bundle names in the words the
#: source used rather than by the ID the catalog computes.
OWN_PREFIXES = ("principal:", "credential:", "artifact:")

Stage = Literal["found", "not-authored", "resolution", "gate", "scored"]
STAGES: tuple[Stage, ...] = get_args(Stage)

#: The stages that are the code's own. ``not-authored`` is outside: it says the
#: oracle could not write the row, which is a limit of this instrument.
MACHINERY: frozenset[str] = frozenset(STAGES) - {"found", "not-authored"}

#: How far a widened quote may reach for a span the source holds once.
MAX_REACH = 240


@dataclass(frozen=True)
class Citation:
    """One source span that locates, or the reason none did."""

    label: str = ""
    quote: str = ""

    @property
    def found(self) -> bool:
        return bool(self.quote)

    def as_quote(self) -> dict[str, str]:
        return {"source_label": self.label, "quote": self.quote}


@dataclass(frozen=True)
class RowCharge:
    """One required row and the stage that did not carry it."""

    row: str
    stage: Stage
    why: str
    subject: str
    predicate: str


@dataclass(frozen=True)
class CaseCharge:
    """One case's oracle run: what it authored, and where each row stopped."""

    case_id: str
    rows: tuple[RowCharge, ...]
    #: Blessed elements the oracle could cite no span for, and the flows that
    #: fell with them.
    uncited: tuple[str, ...] = ()
    #: Signed rows the bundle schema cannot carry.
    inexpressible: tuple[str, ...] = ()
    #: Placements the oracle asserted because the graph requires a zone and the
    #: sources state none.
    scaffolding: int = 0
    #: Blessed elements and flows the resolved graph holds, over the blessed
    #: model's own totals.
    elements: tuple[int, int] = (0, 0)
    flows: tuple[int, int] = (0, 0)

    @property
    def counts(self) -> Counter[str]:
        return Counter(row.stage for row in self.rows)

    @property
    def lost(self) -> tuple[RowCharge, ...]:
        """Every row the deterministic path did not carry."""
        return tuple(row for row in self.rows if row.stage in MACHINERY)

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "required": len(self.rows),
            "stages": {stage: self.counts[stage] for stage in STAGES},
            "uncited": list(self.uncited),
            "inexpressible": list(self.inexpressible),
            "scaffolding": self.scaffolding,
            "elements": list(self.elements),
            "flows": list(self.flows),
        }


def _locates(quote: str, folded) -> bool:
    """Whether one span sits in one source in exactly one place."""
    return bool(spans_for(quote, folded)) and not ambiguous_quote(
        quote, folded.indexed.haystack
    )


def _widened(text: str, start: int, end: int, folded) -> str:
    """The shortest window around one occurrence that the source holds once."""
    for reach in range(0, MAX_REACH, 12):
        candidate = text[max(0, start - reach) : min(len(text), end + reach)].strip()
        if candidate and _locates(candidate, folded):
            return candidate
    return ""


def phrasings(name: str) -> list[str]:
    """What a careful reader looks for, from the whole name down to one word.

    A blessed element carries the name a reviewer settled on, and a source
    rarely spells it that way — case 04 names its public zone only in "expose on
    the internet". A person authoring a bundle reads the sentence that
    introduces the thing, so the oracle tries the name, then each tail of it,
    then its longest words. A weaker search charges the code for a span the
    oracle did not look for.
    """
    words = name.split()
    tried = [name]
    tried += [" ".join(words[start:]) for start in range(1, len(words))]
    tried += sorted(set(words), key=len, reverse=True)
    return [one for one in tried if len(one) > 2]


def cite(name: str, case: GoldenCase, prepared: Mapping[str, Any]) -> Citation:
    """A span naming one element, preferring the shortest one that locates."""
    for wanted in phrasings(name):
        for source in case.sources:
            folded = prepared[source.label]
            lowered = source.text.lower()
            if wanted.lower() not in lowered:
                continue
            if _locates(wanted, folded):
                return Citation(source.label, wanted)
            start = lowered.index(wanted.lower())
            widened = _widened(source.text, start, start + len(wanted), folded)
            if widened:
                return Citation(source.label, widened)
    return Citation()


def named_spans(reference: SignedReference) -> dict[str, list[dict[str, str]]]:
    """Every signed row's spans, by each element the row names.

    A zone is rarely a row's subject and often a row's value — a component's
    membership names it — so a zone borrows the span that places something in
    it. The span is source text either way.
    """
    held: dict[str, list[dict[str, str]]] = {}
    for entry in reference.entries:
        spans = [
            {"source_label": span.source_label, "quote": span.quote}
            for span in entry.support
        ]
        for named in (entry.subject, entry.value):
            if named and spans:
                held.setdefault(named, []).extend(spans)
    return held


def unslugged(named: str) -> str:
    """One of the layer's own subjects, as a bundle names it.

    The catalog slugs a principal, a credential and an artifact out of the words
    a source used, so handing it the reference's computed ID slugs the ID a
    second time and the row lands on a subject nobody named.
    """
    for prefix in OWN_PREFIXES:
        if named.startswith(prefix):
            return named[len(prefix) :]
    return named


def _subject_kind(subject: str, handles: Mapping[str, str]) -> str:
    """Which bundle table one reference subject reads from, or ``""``."""
    if subject in handles:
        return "interaction" if subject.startswith("flow:") else "mention"
    prefix = subject.split(":", 1)[0]
    return prefix if f"{prefix}:" in OWN_PREFIXES else ""


def _roles(model: SystemModel) -> tuple[list, list]:
    """The zones and the placed elements, apart, each with the role that builds it."""
    zones = [
        (boundary, ROLE_FOR[TrustBoundary.__name__, boundary.kind])
        for boundary in model.trust_boundaries
    ]
    zoned = [
        (element, ROLE_FOR[type(element).__name__, getattr(element, "kind", "") or ""])
        for element in model.zoned_elements()
    ]
    return zones, zoned


@dataclass
class _Authored:
    """One case's bundle and what writing it could not do."""

    bundle: SourceFactBundle
    uncited: list[str] = field(default_factory=list)
    inexpressible: list[str] = field(default_factory=list)
    scaffolding: int = 0


def author(case: GoldenCase, reference: SignedReference) -> _Authored:
    """The oracle bundle for one signed case, in local handles."""
    prepared = {
        source.label: span_source(source.label, source.text) for source in case.sources
    }
    spans = named_spans(reference)
    # A zone the sources never name in their own words is cited by a span about
    # something the blessed model puts inside it. Without one the zone is
    # absent, every component of it is unplaced, and one authoring gap reads as
    # a resolver loss.
    for boundary in case.model.trust_boundaries:
        if spans.get(boundary.id):
            continue
        for element in case.model.zoned_elements():
            if element.trust_zone == boundary.id and spans.get(element.id):
                spans[boundary.id] = spans[element.id][:1]
                break

    zones, zoned = _roles(case.model)
    handles: dict[str, str] = {}
    uncited: list[str] = []
    mentions: list[MentionProposal] = []
    for index, (element, role) in enumerate([*zones, *zoned]):
        found = cite(element.name, case, prepared)
        quotes = [found.as_quote()] if found.found else spans.get(element.id, [])[:1]
        if not quotes:
            uncited.append(element.id)
            continue
        handles[element.id] = f"m{index}"
        mentions.append(
            MentionProposal.model_validate(
                {
                    "handle": f"m{index}",
                    "text": element.name,
                    "roles": [role],
                    "quotes": quotes,
                }
            )
        )

    interactions: list[InteractionProposal] = []
    for index, flow in enumerate(case.model.data_flows):
        if flow.source not in handles or flow.destination not in handles:
            uncited.append(flow.id)
            continue
        found = cite(flow.name, case, prepared)
        quotes = [found.as_quote()] if found.found else spans.get(flow.id, [])[:1]
        if not quotes:
            uncited.append(flow.id)
            continue
        handles[flow.id] = f"i{index}"
        interactions.append(
            InteractionProposal.model_validate(
                {
                    "handle": f"i{index}",
                    "initiator": handles[flow.source],
                    "receiver": handles[flow.destination],
                    "action": flow.name,
                    "protocol": flow.protocol,
                    "operations": flow.operations,
                    "quotes": quotes,
                }
            )
        )

    facts: list[FactProposal] = []
    inexpressible: list[str] = []
    for index, entry in enumerate(reference.entries):
        kind = _subject_kind(entry.subject, handles)
        if not kind:
            inexpressible.append(assertion_id(entry))
            continue
        if entry.predicate in GRAPH_REFERENTS and entry.value in (UNKNOWN, ABSENT):
            inexpressible.append(assertion_id(entry))
            continue
        facts.append(_proposal(f"f{index}", kind, entry, handles))

    scaffolding = _placements(case, handles, reference, len(facts))
    return _Authored(
        bundle=SourceFactBundle(
            mentions=mentions, interactions=interactions, facts=[*facts, *scaffolding]
        ),
        uncited=uncited,
        inexpressible=inexpressible,
        scaffolding=len(scaffolding),
    )


def _proposal(
    handle: str, kind: str, entry: Assertion, handles: Mapping[str, str]
) -> FactProposal:
    """One signed row, as the bundle spells it."""
    return FactProposal.model_validate(
        {
            "handle": handle,
            "subject_kind": kind,
            "subject": handles.get(entry.subject) or unslugged(entry.subject),
            "predicate": entry.predicate,
            "value": handles.get(entry.value) or unslugged(entry.value),
            "reason": entry.reason,
            "scope": [qualifier.model_dump() for qualifier in entry.scope],
            "basis": entry.basis,
            "explanation": entry.explanation,
            "exclusive": entry.exclusive,
            "quotes": [
                {"source_label": span.source_label, "quote": span.quote}
                for span in entry.support
            ],
        }
    )


def _placements(
    case: GoldenCase,
    handles: Mapping[str, str],
    reference: SignedReference,
    start: int,
) -> list[FactProposal]:
    """A membership row per component the reference places nowhere.

    **The cost the schema imposes, made visible.** ``trust_zone`` is required,
    so a component the sources place nowhere reaches the graph only if something
    asserts a zone for it. The oracle asserts the one the blessed model holds and
    marks it ``inferred``; the matcher then charges it against the reference's
    own ``unknown``, which is the right answer and the point.
    """
    placed = {
        entry.subject
        for entry in reference.entries
        if entry.predicate == "network-membership"
        and entry.value not in (UNKNOWN, ABSENT)
    }
    rows: list[FactProposal] = []
    for element in case.model.zoned_elements():
        if element.id in placed or element.id not in handles:
            continue
        if element.trust_zone not in handles:
            continue
        rows.append(
            FactProposal.model_validate(
                {
                    "handle": f"s{start + len(rows)}",
                    "subject_kind": "mention",
                    "subject": handles[element.id],
                    "predicate": "network-membership",
                    "value": handles[element.trust_zone],
                    "basis": "inferred",
                    "explanation": (
                        "the graph requires a zone and no source states one for"
                        " this component"
                    ),
                }
            )
        )
    return rows


def charge(case: GoldenCase, reference: SignedReference) -> CaseCharge:
    """Put one case's oracle bundle through the path, and charge every row."""
    written = author(case, reference)
    sources = {source.label: source.text for source in case.sources}
    resolution = resolve_bundle(written.bundle, sources)
    result = AssertionResult(
        case_id=case.id,
        proposal={},
        catalog=resolution.record.catalog,
        issues=tuple(resolution.record.issues),
    )
    graded = replay_assertions(case, reference, result)

    required = set(required_rows(reference))
    fates = {row.reference: row.fate for row in graded.rows}
    dispositions = {row.handle: row for row in resolution.dispositions}
    carried = {row.handle for row in written.bundle.facts}
    kept = {assertion_id(row) for row in resolution.record.catalog.entries}

    charged: list[RowCharge] = []
    for index, entry in enumerate(reference.entries):
        row = assertion_id(entry)
        if row not in required:
            continue
        stage, why = _stage_of(
            row, f"f{index}", carried, dispositions, kept, fates.get(row, "omitted")
        )
        charged.append(RowCharge(row, stage, why, entry.subject, entry.predicate))

    blessed = {element.id for element in case.model.zoned_elements()} | {
        boundary.id for boundary in case.model.trust_boundaries
    }
    built = {element.id for element in resolution.model.zoned_elements()} | {
        boundary.id for boundary in resolution.model.trust_boundaries
    }
    blessed_flows = {flow.id for flow in case.model.data_flows}
    built_flows = {flow.id for flow in resolution.model.data_flows}
    return CaseCharge(
        case_id=case.id,
        rows=tuple(charged),
        uncited=tuple(written.uncited),
        inexpressible=tuple(written.inexpressible),
        scaffolding=written.scaffolding,
        elements=(len(built & blessed), len(blessed)),
        flows=(len(built_flows & blessed_flows), len(blessed_flows)),
    )


def _stage_of(
    row: str,
    handle: str,
    carried: frozenset[str] | set[str],
    dispositions: Mapping[str, Any],
    kept: frozenset[str] | set[str],
    fate: str,
) -> tuple[Stage, str]:
    """The first stage that did not carry one row, and why."""
    if fate == "found":
        return "found", ""
    if handle not in carried:
        return "not-authored", "the oracle wrote no row for it"
    landed = dispositions.get(handle)
    if landed is not None and landed.disposition not in ("consumed", "preserved"):
        return "resolution", landed.code
    if row not in kept:
        return "gate", "the catalog does not hold it"
    return "scored", fate


def charges(corpus_dir: Path) -> list[CaseCharge]:
    """Every signed case, charged. A case nobody signed is skipped."""
    found = []
    for case in load_corpus(corpus_dir):
        reference = signed_reference(corpus_dir, case)
        if reference is not None:
            found.append(charge(case, reference))
    return found


def render(found: Sequence[CaseCharge]) -> str:
    """The per-stage table, as text."""
    lines = [
        "## The ceiling a perfect reading reaches",
        "",
        (
            "Every required row of every signed case, charged to the first stage"
            " that did not carry it. `not-authored` is the oracle's own limit"
            " and never the code's; the three stages after it are the"
            " deterministic path's."
        ),
        "",
        "| case | required | " + " | ".join(STAGES) + " | elements | flows |",
        "| --- | --- |" + " --- |" * (len(STAGES) + 2),
    ]
    totals: Counter[str] = Counter()
    for case in found:
        counts = case.counts
        totals.update(counts)
        lines.append(
            f"| {case.case_id} | {len(case.rows)} | "
            + " | ".join(str(counts[stage]) for stage in STAGES)
            + f" | {case.elements[0]}/{case.elements[1]}"
            f" | {case.flows[0]}/{case.flows[1]} |"
        )
    required = sum(len(case.rows) for case in found)
    lines += [
        "",
        (
            f"**{totals['found']} of {required} required rows survive.** The"
            f" deterministic path lost"
            f" {sum(totals[stage] for stage in MACHINERY)}."
        ),
        "",
        (
            f"{sum(len(case.inexpressible) for case in found)} signed row(s) the"
            " bundle cannot carry, and"
            f" {sum(case.scaffolding for case in found)} placement(s) the oracle"
            " asserted because the graph requires a zone the sources never state."
        ),
        "",
    ]
    for case in found:
        for row in case.lost:
            lines.append(
                f"- `{case.case_id}` {row.stage}: {row.subject}"
                f" {row.predicate} — {row.why}"
            )
    return "\n".join(lines) + "\n"


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--corpus", default="evals/corpus", help="the corpus directory to read"
    )


def command_oracle(args: argparse.Namespace) -> int:
    """Report the ceiling. It runs no model and reads no credential."""
    print(render(charges(Path(args.corpus))), end="")
    return 0
