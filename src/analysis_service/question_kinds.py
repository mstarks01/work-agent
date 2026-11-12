"""The kinds of question the critic may ask about an element, as a closed table.

The critic names an open fact with no place in the model as a free-text
``subject``, and its words change from one sample to the next: 77 of 79 such
questions on one sweep were distinct texts. Their kinds do not: the same 79
fell into 11 kinds, and a table read off the first six cases covered 40 of the
46 questions after them (``QA-2026-09-26-03-E8``). So the critic picks a kind
from this table and the element it is about, and both come from closed sets. A
question with no fitting kind still falls back to a ``subject``, and how often
it does is what says whether this table is enough.

**Data only.** :mod:`analysis_service.claims` puts the IDs into the provider
schema and :mod:`analysis_service.questions` renders and answers them, so this
module imports neither.

**The maintainer reviewed the wording.** An agent drafted every row from the
typed questions, and ``reviewed_by`` names who accepted it. A new row carries
``None`` until the maintainer reads it. ``answer`` follows from the wording: a
question that opens with "Are", "Can" or "Does" asks whether something holds.
A compound question is answered in its facets, which the maintainer reviewed
with the kinds on 2026-09-29 (#1289).

**Each part is a predicate** (ADR 0117). A facet, and a ``yes-no`` kind, names
the :data:`~analysis_service.assertions.REGISTRY` row that holds its question,
the protection a Control states and the row each answer writes. A kind is the
group of predicates that a page asks together about one element.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from analysis_service.assertions import (
    REGISTRY,
    OwnerQuestion,
    PredicateName,
    ProtectiveAnswer,
)
from analysis_service.parsing import member

__all__ = [
    "ALL_FACET_IDS",
    "MAX_FACETS",
    "QUESTION_KINDS",
    "Facet",
    "QuestionKind",
    "QuestionKindName",
    "parse_question_kind",
]


#: The name of each kind of :data:`QUESTION_KINDS`.
QuestionKindName = Literal[
    "capacity-limits",
    "audit-evidence",
    "authorization-scope",
    "stored-copy-integrity",
    "stored-copy-access",
    "reachability",
    "provenance",
    "code-execution",
    "physical-access",
    "content-validation",
    "destination",
    "security-configuration",
    "failure-handling",
    "update-process",
    "data-exposure",
]


@dataclass(frozen=True)
class Facet:
    """One short part of a compound question kind, answered yes, no, not
    applicable or "I don't know"."""

    #: Stable within its kind; an answer names the facet by it.
    id: str
    #: The predicate an answer to this part writes.
    predicate: PredicateName

    @property
    def question(self) -> str:
        """The part as a question, with "it" for the element."""
        question = _asked(self.predicate).question
        if question is None:
            raise KeyError(f"{self.predicate!r} has no part question")
        return question

    @property
    def statement(self) -> str:
        """The part as a protection that a Control states, with no subject."""
        return _asked(self.predicate).statement

    @property
    def protects(self) -> ProtectiveAnswer:
        """The answer that makes the part a protection."""
        return _asked(self.predicate).protects


@dataclass(frozen=True)
class QuestionKind:
    """One kind of question about one element of the model."""

    #: The question, with ``{element}`` where the element's name goes.
    template: str
    #: What the kind covers, for the critic choosing among kinds.
    covers: str
    #: A short name of what the kind covers, as the Controls page groups it.
    topic: str
    #: Who reviewed the wording, or ``None`` where nobody has.
    reviewed_by: str | None
    #: How the question is answered: ``yes-no`` where it asks whether one
    #: thing holds, so a page offers yes, no or "I don't know"; ``facets``
    #: where it asks several things, each a :class:`Facet`; ``text`` where it
    #: asks who, what or how and no facet fits. No default, so a new row says
    #: which.
    answer: Literal["yes-no", "facets", "text"]
    #: The predicate an answer to a ``yes-no`` kind writes, and ``None`` for
    #: every other: a ``facets`` kind names one for each facet.
    predicate: PredicateName | None
    #: The parts a ``facets`` kind is answered in, and empty for every other.
    facets: tuple[Facet, ...]

    @property
    def statement(self) -> str | None:
        """The protection a Control states of a ``yes-no`` kind, and ``None``
        for every other: a ``facets`` kind states each facet's."""
        return None if self.predicate is None else _asked(self.predicate).statement

    @property
    def protects(self) -> ProtectiveAnswer | None:
        """The answer to a ``yes-no`` kind that makes it a protection, and
        ``None`` for every other."""
        return None if self.predicate is None else _asked(self.predicate).protects


def _asked(predicate: PredicateName) -> OwnerQuestion:
    """How a page asks ``predicate``. Every predicate this table names has one,
    and ``tests/test_question_kinds.py`` holds it."""
    asked = REGISTRY[predicate].asked
    if asked is None:
        raise KeyError(f"{predicate!r} is asked by no question kind")
    return asked


QUESTION_KINDS: Mapping[QuestionKindName, QuestionKind] = MappingProxyType(
    {
        "capacity-limits": QuestionKind(
            template="What limits bound the requests and work {element} accepts:"
            " rate, size, concurrency, quotas or queue bounds?",
            covers="flooding, exhaustion, missing quotas or load shedding",
            topic="Request limits",
            reviewed_by="mstarks01",
            answer="facets",
            predicate=None,
            facets=(
                Facet("rate", "capacity-limits-rate"),
                Facet("size", "capacity-limits-size"),
                Facet("concurrency", "capacity-limits-concurrency"),
                Facet("quota", "capacity-limits-quota"),
            ),
        ),
        "audit-evidence": QuestionKind(
            template="What record shows who acted on {element}, and who can"
            " change or delete that record?",
            covers="attribution, audit trails, evidence that survives a dispute",
            topic="Audit records",
            reviewed_by="mstarks01",
            answer="facets",
            predicate=None,
            facets=(
                Facet("records-actor", "record-attribution"),
                Facet("record-protected", "audit-evidence-record-protected"),
            ),
        ),
        "authorization-scope": QuestionKind(
            template="Who may do what on {element}, and does it check each"
            " request against that?",
            covers="per-request authorization, roles, object ownership, grant scope",
            topic="Authorization",
            reviewed_by="mstarks01",
            answer="facets",
            predicate=None,
            facets=(
                Facet("checks-each-request", "authorization-scope-checks-each-request"),
                Facet("least-privilege", "authorization-scope-least-privilege"),
                Facet("checks-ownership", "authorization-scope-checks-ownership"),
            ),
        ),
        "stored-copy-integrity": QuestionKind(
            template="Are backups, snapshots and restores of {element} checked"
            " for tampering before they are used?",
            covers="integrity of backups, snapshots, restores and stored media",
            topic="Backup integrity",
            reviewed_by="mstarks01",
            answer="yes-no",
            predicate="stored-copy-integrity",
            facets=(),
        ),
        "stored-copy-access": QuestionKind(
            template="Who can obtain a disk, backup or snapshot copy of {element}?",
            covers="access to the storage layer beneath an application",
            topic="Backup access",
            reviewed_by="mstarks01",
            answer="facets",
            predicate=None,
            facets=(
                Facet("encrypted", "stored-copy-access-encrypted"),
                Facet("restricted", "stored-copy-access-restricted"),
            ),
        ),
        "reachability": QuestionKind(
            template="Who can reach {element}'s interface, and from where?",
            covers="who can reach an interface where no `exposure` attribute answers it",
            topic="Reachability",
            reviewed_by="mstarks01",
            answer="facets",
            predicate=None,
            facets=(
                Facet("internet", "internet-exposure"),
                Facet("named-callers", "reachability-named-callers"),
            ),
        ),
        "provenance": QuestionKind(
            template="Does {element} check where what it takes came from, and"
            " that it was not altered?",
            covers="signatures, origin checks, supply-chain provenance",
            topic="Provenance",
            reviewed_by="mstarks01",
            answer="facets",
            predicate=None,
            facets=(
                Facet("sender", "provenance-sender"),
                Facet("integrity", "provenance-integrity"),
            ),
        ),
        "code-execution": QuestionKind(
            template="Can content that {element} takes run code with its authority?",
            covers="loaders, build steps or parsers that execute supplied content",
            topic="Code execution",
            reviewed_by="mstarks01",
            answer="yes-no",
            predicate="code-execution",
            facets=(),
        ),
        "physical-access": QuestionKind(
            template="Can someone with physical access to {element} read its"
            " secrets or change it?",
            covers="devices and hosts outside the operator's physical control",
            topic="Physical access",
            reviewed_by="mstarks01",
            answer="yes-no",
            predicate="physical-access",
            facets=(),
        ),
        "content-validation": QuestionKind(
            template="Does {element} check the values it takes against what it"
            " expects, and encode or bind them where it uses them?",
            covers="input validation, output encoding, query construction",
            topic="Input handling",
            reviewed_by="mstarks01",
            answer="facets",
            predicate=None,
            facets=(
                Facet("validates", "content-validation-validates"),
                Facet("encodes", "content-validation-encodes"),
            ),
        ),
        "destination": QuestionKind(
            template="Does {element} check that what it sends reaches the"
            " intended recipient?",
            covers="delivery to the right party, stale or altered destinations",
            topic="Delivery",
            reviewed_by="mstarks01",
            answer="yes-no",
            predicate="destination",
            facets=(),
        ),
        "security-configuration": QuestionKind(
            template="Which security settings does {element} use, such as cookie"
            " attributes, response headers, cross-origin rules or secret storage?",
            covers="configuration a deployment sets rather than a design states",
            topic="Security settings",
            reviewed_by="mstarks01",
            answer="facets",
            predicate=None,
            facets=(
                Facet("cookies", "security-configuration-cookies"),
                Facet("headers", "security-configuration-headers"),
                Facet("cross-origin", "security-configuration-cross-origin"),
                Facet("secret-store", "security-configuration-secret-store"),
            ),
        ),
        "failure-handling": QuestionKind(
            template="What happens when {element} fails or stops, and who notices?",
            covers="failure detection, silent staleness, error handling",
            topic="Failures",
            reviewed_by="mstarks01",
            answer="facets",
            predicate=None,
            facets=(
                Facet("fails-closed", "failure-handling-fails-closed"),
                Facet("alerts", "failure-handling-alerts"),
                Facet("recovers", "failure-handling-recovers"),
            ),
        ),
        "update-process": QuestionKind(
            template="How are {element}'s components kept up to date?",
            covers="patching, update windows, remediation time frames",
            topic="Updates",
            reviewed_by="mstarks01",
            answer="facets",
            predicate=None,
            facets=(
                Facet("scheduled", "update-process-scheduled"),
                Facet("vulnerability-alerts", "update-process-vulnerability-alerts"),
                Facet("tested", "update-process-tested"),
            ),
        ),
        "data-exposure": QuestionKind(
            template="Which data does {element} return or record, and who can see it?",
            covers="response fields, data in URLs or logs, classification",
            topic="Data exposure",
            reviewed_by="mstarks01",
            answer="facets",
            predicate=None,
            facets=(
                Facet("minimal-fields", "data-exposure-minimal-fields"),
                Facet("clean-logs", "data-exposure-clean-logs"),
                Facet("restricted-readers", "data-exposure-restricted-readers"),
            ),
        ),
    }
)


def parse_question_kind(name: str) -> QuestionKindName | None:
    """The kind of :data:`QUESTION_KINDS` that ``name`` names, or ``None``.

    A reference holds the kind as a ``str``, so a report that names a kind
    this table does not hold still loads. Each reader that calls this decides
    what that reference gets: a check refuses it, and a label shows the kind
    as it is written.
    """
    return member(name, QUESTION_KINDS)


#: Every facet ID some kind has, which a reference may name.
ALL_FACET_IDS: frozenset[str] = frozenset(
    facet.id for kind in QUESTION_KINDS.values() for facet in kind.facets
)

#: The most facets one kind has, and so the most one reference names.
MAX_FACETS = max(len(kind.facets) for kind in QUESTION_KINDS.values())
