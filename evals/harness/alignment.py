"""One alignment between a reference model and a produced one, read by every scorer.

## Why one object

Which produced element stands for which reference element is one question,
and every figure that asks it reads the answer here: strict recall, the
attribute join, the initiator and actor readings, the zone partition and the
end-to-end loss attribution. A figure resolving identity on its own is how a
reader's ruling reaches one number and not the next — the audit's probe on
corpus case 09 is an approved alias that moved ``sourced_recall`` and left
``initiator_recall`` at 0.0 with every incident interaction charged as lost
(#961).

This module pairs reference elements with produced ones **one to one**,
records the evidence each pair rests on, and lists what it could not decide
rather than guessing.

## The rules, in order

1. **Exact.** The same ID on both sides. Nodes and zones first, then flows.
2. **Alias.** A node or zone under a name a reader ruled supported, from the
   case's own ``aliases``. The alias keeps its element's type, and a produced
   element already paired exactly is not available to an alias.
3. **Membership.** A zone under any name, when one unpaired produced zone
   holds more of the reference zone's paired members than any other. A zone's
   name is a word the model coins for a partition; which elements sit in it
   is the fact, and the partition-agreement figure already reads it that way.
   A tie, or one produced zone that two reference zones both claim, is listed
   and paired with nothing.
4. **Label.** A flow between aligned endpoints carrying the reference flow's
   own label, or a label a reader ruled an alias for that flow. The endpoints
   resolved through rules 1 to 3, so a flow whose endpoint was renamed under a
   ruling is still that flow.
5. **Discriminated.** A flow between aligned endpoints under a different label,
   when exactly one reference flow and exactly one produced flow there agree
   on every discriminator in :data:`FLOW_DISCRIMINATORS`. A label is a word the
   model coins for an interaction; the operation and whether a protocol is
   stated are facts about it.
6. **Sole.** The only reference flow and the only produced flow between an
   aligned endpoint pair, when the labels and the discriminators both differ.
   One interaction on each side between two found elements is one interaction
   described twice, and the facts that differ are charged as wrong facts on
   the pair rather than as an element lost and an element invented (#961 step
   6). Only where each side holds one flow in all: a produced interaction
   beside a paired one is not the sole flow, so a fabricated parallel stays
   unpaired.

Anything else stays unaligned. Two flows on either side that the discriminators
cannot tell apart are recorded in :attr:`Alignment.ambiguous` and paired with
nothing, so a split or a merge is a listed fact rather than a silent choice.

## What this is not

Not a fuzzy match. No rule reads name similarity. Rules 3 and 6 read
structure — who sits in a zone, what sits between two endpoints — and each
pair records which rule made it, so a figure can be read with or without
them. A produced interaction beside a real one between the same endpoints is
still an invention: rule 6 pairs only where each side holds one in all. The
strict figures stay beside the aligned ones, so nothing here rewrites a
number an archived sweep reported.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from analysis_service.analysis import states_a_protocol
from analysis_service.system_model import (
    DataFlow,
    SystemModel,
    make_element_id,
    normalize_name,
)
from evals.harness.reference import GoldenCase

#: What a pair rests on, in the order the rules run.
Evidence = Literal["exact", "alias", "membership", "label", "discriminated", "sole"]
EVIDENCE: tuple[Evidence, ...] = (
    "exact",
    "alias",
    "membership",
    "label",
    "discriminated",
    "sole",
)


def protocol_state(value: Any) -> str:
    """A flow's protocol reduced to whether it says anything: ``stated`` or ``silent``.

    The one reader of that reduction, shared by the scored-attribute table in
    :mod:`evals.harness.modes` and by :data:`FLOW_DISCRIMINATORS`, so the two
    cannot disagree about what a silent protocol is.
    """
    return "stated" if states_a_protocol(value) else "silent"


#: The facts that tell two interactions between one pair of endpoints apart,
#: each with the reduction it is compared under. ``operations`` is a closed
#: vocabulary and compares as written. ``protocol`` is free text, so only
#: whether it is stated compares — ``HTTPS`` against ``HTTP over TLS`` is one
#: fact worded twice, and a state is what the scorer compares it on too.
FLOW_DISCRIMINATORS: Mapping[str, Callable[[Any], str]] = {
    "operations": str,
    "protocol": protocol_state,
}


def placeholder_zones(model: SystemModel) -> frozenset[str]:
    """The zones a reference holds only because the schema requires one.

    A blessed zone whose every member's placement is an assumption the model
    records — the schema requires a zone and the source gives none — is a
    placement, not a fact the source states, and a reader has ruled that a
    producer is not required to draw it (#961 step 6). Read off
    :meth:`~analysis_service.system_model.SystemModel.assumed_zone_elements`,
    the one reader of which placements are inferred, so the rule has no
    second spelling here. A zone with no member is not a placeholder: nothing
    says why it exists.
    """
    assumed = model.assumed_zone_elements()
    members: dict[str, list[str]] = defaultdict(list)
    for element in model.zoned_elements():
        if element.trust_zone:
            members[element.trust_zone].append(element.id)
    return frozenset(
        zone.id
        for zone in model.trust_boundaries
        if members[zone.id] and all(one in assumed for one in members[zone.id])
    )


def singular(word: str) -> str:
    """One word with a plural ``s`` dropped, so ``servers`` and ``server`` are one.

    Public because it has a second reader: ``evals/critic_review/replay.py``
    matches a rejection reason against the anchors a reader named, and a critic
    writing "queues" where the anchor says "queue" engaged with the fact
    either way.

    Deliberately crude: three letters of stem before the ``s``, and no other
    ending. It serves a check that must not accuse, so under-stemming leaves a
    pair looking different and over-stemming never invents a match that a
    reader would dispute.
    """
    return (
        word[:-1] if len(word) > 3 and word.endswith("s") and word[-2] != "s" else word
    )


def slug_key(element_id: str) -> str:
    """One element ID with each slug segment singularised.

    ``entity:analysts`` and ``entity:analyst`` are one key. Which of the two an
    extraction should write is ``extract.md``'s rule and is measured as naming
    conformity; charging it as a different component would count one
    disagreement twice.
    """
    prefix, _, slug = element_id.partition(":")
    return f"{prefix}:{'-'.join(singular(part) for part in slug.split('-'))}"


def element_type(element_id: str) -> str:
    """An element ID's type prefix — ``store`` of ``store:feature-store``.

    The whole ID where it carries no prefix, which no model this reads
    produces: the type is part of the derived ID. Returning the ID itself keeps
    a malformed one comparable only to itself, rather than pooling every
    malformed ID under one empty type.
    """
    return element_id.split(":", 1)[0] if ":" in element_id else element_id


@dataclass(frozen=True)
class Pair:
    """One reference element and the produced element that stands for it."""

    reference: str
    produced: str
    evidence: Evidence
    #: The reader's words behind an ``alias`` pair. Empty under any other
    #: evidence, because no other rule rests on a person's ruling.
    excerpt: str = ""

    def to_json(self) -> dict[str, str]:
        return {
            "reference": self.reference,
            "produced": self.produced,
            "evidence": self.evidence,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class Ambiguity:
    """Elements no one-to-one rule could decide between, on both sides.

    For flows: interactions between one aligned endpoint pair that the
    discriminators do not tell apart. For nodes: two alias rulings that derive
    one produced element. Listed, and paired with nothing.
    """

    reference: tuple[str, ...]
    produced: tuple[str, ...]

    def to_json(self) -> dict[str, list[str]]:
        return {"reference": list(self.reference), "produced": list(self.produced)}


@dataclass(frozen=True)
class Alignment:
    """Every pair the rules made, and everything they left.

    ``unaligned_reference`` and ``unaligned_produced`` are the two sides'
    leftovers, every element of the model that is in no pair. ``ambiguous``
    names the leftovers that had a candidate and lost it to a second one.
    """

    pairs: tuple[Pair, ...]
    unaligned_reference: tuple[str, ...]
    unaligned_produced: tuple[str, ...]
    ambiguous: tuple[Ambiguity, ...] = ()

    @classmethod
    def empty(cls) -> Alignment:
        """No pairs and nothing to pair: the alignment of a model nobody produced."""
        return cls((), (), ())

    @property
    def produced_of(self) -> Mapping[str, str]:
        """Each reference ID against the produced ID that stands for it."""
        return {pair.reference: pair.produced for pair in self.pairs}

    @property
    def reference_of(self) -> Mapping[str, str]:
        """Each produced ID against the reference ID it stands for."""
        return {pair.produced: pair.reference for pair in self.pairs}

    @property
    def aligned_reference(self) -> frozenset[str]:
        return frozenset(self.produced_of)

    def by_evidence(self, evidence: Evidence) -> tuple[Pair, ...]:
        return tuple(pair for pair in self.pairs if pair.evidence == evidence)

    def to_json(self) -> dict[str, Any]:
        return {
            "pairs": [pair.to_json() for pair in self.pairs],
            "unaligned_reference": list(self.unaligned_reference),
            "unaligned_produced": list(self.unaligned_produced),
            "ambiguous": [entry.to_json() for entry in self.ambiguous],
        }


def align(case: GoldenCase, produced: SystemModel | None) -> Alignment:
    """Pair the case's blessed model with a produced one, by the rules above."""
    if produced is None:
        return Alignment((), tuple(element.id for element in case.model.elements()), ())
    reference_ids = [element.id for element in case.model.elements()]
    produced_ids = [element.id for element in produced.elements()]
    pairs: list[Pair] = []
    ambiguous: list[Ambiguity] = []
    _align_nodes(case, reference_ids, produced_ids, pairs, ambiguous)
    _align_zones(case.model, produced, pairs, ambiguous)
    _align_flows(case, produced, pairs, ambiguous)
    aligned = {pair.reference for pair in pairs}
    found = {pair.produced for pair in pairs}
    return Alignment(
        tuple(pairs),
        tuple(element_id for element_id in reference_ids if element_id not in aligned),
        tuple(element_id for element_id in produced_ids if element_id not in found),
        tuple(ambiguous),
    )


def _align_nodes(
    case: GoldenCase,
    reference_ids: Iterable[str],
    produced_ids: Iterable[str],
    pairs: list[Pair],
    ambiguous: list[Ambiguity],
) -> None:
    """Rules 1 and 2 over everything that is not a flow."""
    nodes = frozenset(_nodes(reference_ids))
    held = frozenset(_nodes(produced_ids))
    pairs.extend(Pair(one, one, "exact") for one in sorted(nodes & held))
    by_key = {slug_key(element_id): element_id for element_id in held - nodes}
    claims: dict[tuple[str, str], str] = {}
    for alias in case.meta.aliases:
        if alias.element in held or alias.element not in nodes:
            continue
        found = by_key.get(
            slug_key(make_element_id(element_type(alias.element), alias.name))
        )
        if found is not None and element_type(found) == element_type(alias.element):
            claims.setdefault((alias.element, found), alias.excerpt)
    # One to one on both sides: a produced element two rulings derive, and a
    # blessed element two of its rulings found, are each listed and not paired.
    per_reference = Counter(reference for reference, _ in claims)
    per_found = Counter(found for _, found in claims)
    for (reference, found), excerpt in sorted(claims.items()):
        if per_reference[reference] == 1 and per_found[found] == 1:
            pairs.append(Pair(reference, found, "alias", excerpt))
    for found in sorted(name for name, count in per_found.items() if count > 1):
        ambiguous.append(
            Ambiguity(tuple(sorted(r for r, f in claims if f == found)), (found,))
        )
    for reference in sorted(name for name, count in per_reference.items() if count > 1):
        ambiguous.append(
            Ambiguity(
                (reference,), tuple(sorted(f for r, f in claims if r == reference))
            )
        )


def _nodes(element_ids: Iterable[str]) -> Iterable[str]:
    return (
        element_id
        for element_id in element_ids
        if element_type(element_id) != DataFlow.id_prefix
    )


def _align_zones(
    reference: SystemModel,
    produced: SystemModel,
    pairs: list[Pair],
    ambiguous: list[Ambiguity],
) -> None:
    """Rule 3: an unpaired zone by where its paired members sit."""
    produced_of = {pair.reference: pair.produced for pair in pairs}
    taken = {pair.produced for pair in pairs}
    zone_of = {
        element.id: element.trust_zone
        for element in produced.zoned_elements()
        if element.trust_zone
    }
    held = {zone.id for zone in produced.trust_boundaries} - taken
    claims: dict[str, str] = {}
    for zone in reference.trust_boundaries:
        if zone.id in produced_of:
            continue
        votes = Counter(
            zone_of[produced_of[member.id]]
            for member in reference.zoned_elements()
            if member.trust_zone == zone.id
            and member.id in produced_of
            and zone_of.get(produced_of[member.id]) in held
        )
        if not votes:
            continue
        ranked = votes.most_common()
        top = [candidate for candidate, n in ranked if n == ranked[0][1]]
        if len(top) == 1:
            claims[zone.id] = top[0]
        else:
            ambiguous.append(Ambiguity((zone.id,), tuple(sorted(top))))
    per_produced = Counter(claims.values())
    for reference_id, produced_id in sorted(claims.items()):
        if per_produced[produced_id] == 1:
            pairs.append(Pair(reference_id, produced_id, "membership"))
    for produced_id in sorted(p for p, n in per_produced.items() if n > 1):
        ambiguous.append(
            Ambiguity(
                tuple(sorted(r for r, p in claims.items() if p == produced_id)),
                (produced_id,),
            )
        )


def _align_flows(
    case: GoldenCase,
    produced: SystemModel,
    pairs: list[Pair],
    ambiguous: list[Ambiguity],
) -> None:
    """Rules 3 and 4, over the endpoint pairs the node rules resolved.

    A flow alias is another label a reader ruled supported for one blessed
    flow, and it is read between the same aligned endpoints the label rule
    reads: a produced flow there under the alias's label pairs as ``alias``,
    with the reader's excerpt. It never reaches across endpoints, because a
    flow under a ruled label between other elements is a different flow.
    """
    reference = case.model
    aliased: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for alias in case.meta.aliases:
        if element_type(alias.element) == "flow":
            aliased[alias.element].append((normalize_name(alias.name), alias.excerpt))
    produced_of = {pair.reference: pair.produced for pair in pairs}
    theirs: dict[tuple[str, str], list[DataFlow]] = defaultdict(list)
    for flow in produced.data_flows:
        theirs[flow.source, flow.destination].append(flow)
    ours: dict[tuple[str, str], list[DataFlow]] = defaultdict(list)
    for flow in reference.data_flows:
        source = produced_of.get(flow.source)
        destination = produced_of.get(flow.destination)
        if source is not None and destination is not None:
            ours[source, destination].append(flow)
    for endpoints, reference_flows in sorted(ours.items()):
        candidates = theirs.get(endpoints, [])
        by_label = {_label(flow): flow for flow in candidates}
        rest: list[DataFlow] = []
        for flow in reference_flows:
            found = by_label.pop(_label(flow), None)
            if found is not None:
                evidence: Evidence = "exact" if found.id == flow.id else "label"
                pairs.append(Pair(flow.id, found.id, evidence))
                continue
            ruled = next(
                (
                    (by_label.pop(label), excerpt)
                    for label, excerpt in aliased[flow.id]
                    if label in by_label
                ),
                None,
            )
            if ruled is None:
                rest.append(flow)
            else:
                pairs.append(Pair(flow.id, ruled[0].id, "alias", ruled[1]))
        left = list(by_label.values())
        alone = len(reference_flows) == 1 and len(candidates) == 1
        if alone and len(rest) == 1 and len(left) == 1:
            evidence = (
                "discriminated"
                if _signature(rest[0]) == _signature(left[0])
                else "sole"
            )
            pairs.append(Pair(rest[0].id, left[0].id, evidence))
            continue
        _discriminate(rest, left, pairs, ambiguous)


def _label(flow: DataFlow) -> str:
    """The describing half of a flow ID, which is the last of its segments."""
    return flow.id.rsplit(":", 1)[-1]


def _signature(flow: DataFlow) -> tuple[str, ...]:
    return tuple(
        reduce(getattr(flow, attribute))
        for attribute, reduce in FLOW_DISCRIMINATORS.items()
    )


def _discriminate(
    reference_flows: list[DataFlow],
    candidates: list[DataFlow],
    pairs: list[Pair],
    ambiguous: list[Ambiguity],
) -> None:
    """Rule 4: one reference flow against one produced flow, or a listed ambiguity."""
    ours: dict[tuple[str, ...], list[DataFlow]] = defaultdict(list)
    for flow in reference_flows:
        ours[_signature(flow)].append(flow)
    theirs: dict[tuple[str, ...], list[DataFlow]] = defaultdict(list)
    for flow in candidates:
        theirs[_signature(flow)].append(flow)
    for signature, flows in sorted(ours.items()):
        found = theirs.get(signature)
        if not found:
            continue
        if len(flows) == 1 and len(found) == 1:
            pairs.append(Pair(flows[0].id, found[0].id, "discriminated"))
        else:
            ambiguous.append(
                Ambiguity(
                    tuple(sorted(flow.id for flow in flows)),
                    tuple(sorted(flow.id for flow in found)),
                )
            )
