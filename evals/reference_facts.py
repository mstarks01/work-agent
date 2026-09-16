"""The reference facts a corpus case's sources establish, one row per predicate.

``evals/corpus/<case>/facts.json`` holds what a reader says a case's sources
state, in the shape the assertion node proposes: a subject, a predicate, a
value, a scope, a basis and the quotes that carry it. :func:`reference_catalog`
resolves those rows through :func:`analysis_service.assertions.resolve_catalog`,
the one reader of what a proposed row means, so a reference row is held to
every rule a produced row is — its quote is located in the source, its subject
snaps to the blessed model, its value is one the predicate admits — and a row
the gate would refuse is a corpus error rather than a reference.

**A row is a draft until a person signs it.** ``drafted_by`` names the agent
that wrote the row and its proposed answer. ``reviewed_by`` names the person
who ruled on it, and is ``None`` until one does. ``tests/test_reference_facts.py``
reads both: the drafter may never sign, and a case whose rows are unsigned is
named rather than trusted. Nothing here decides a fact. That is the reviewer's
act, and the field records whose it was.

The file carries a second list. ``disputed`` names each value the blessed
model holds that the drafter reads the source as not establishing, with the
value proposed instead and the kind of gap. It is the adjudication table
issue #961 asks for, kept in a field so that an entry the model no longer
matches fails a lint rather than lingering in prose. An entry waits until a
person rules on it. A ruling to change the model removes the entry when the
model changes, and a ruling to keep the model stays as a signed entry.

Two conventions the rows follow, because the projection reads them. A fact
that projects into a graph field — a mechanism, a transport, a zone, an
exposure — sits on the graph subject, unscoped and under one predicate, so
:func:`~analysis_service.assertions.project` writes a value rather than a
degraded ``unknown``; a credential is recorded on the principal that presents
it rather than as a second predicate on the interaction. An ``unknown`` row is
written where the source raises the predicate and does not answer it, which is
the ``hedged`` reason, and not for every predicate the source is silent about.
That is the rule ``prompts/assert.md`` gives the producer, so the reference and
the produced catalog enumerate the same questions.

A value the schema requires and the source never gives — a zone for an element
nothing places — is a placeholder, not a fact. The model records it in its
assumptions list, and the reference row for that pair reads ``unknown``, so a
producer's placement is not graded as a fact the source states. A justified
inference is different: its row carries an inferred basis and an explanation,
and it agrees with the model. ``tests/test_reference_facts.py`` tells the two
apart by the row, and admits neither where the model records no assumption.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from analysis_service.assertions import (
    GRAPH_BOUND,
    SUBJECT_PREFIXES,
    AssertionCatalog,
    AssertionProposal,
    CatalogProposal,
    QualifierKind,
    catalog_issues,
    resolve_catalog,
    subject_id,
)
from analysis_service.system_model import SystemModel
from evals.harness.reference import CorpusError

#: The ID prefixes of the graph-bound subject types, whose aliases are the
#: case's element aliases, and of the layer's own, which this file may alias.
GRAPH_BOUND_PREFIXES: frozenset[str] = frozenset().union(
    *(SUBJECT_PREFIXES[kind] for kind in GRAPH_BOUND)
)
OWN_PREFIXES: frozenset[str] = frozenset().union(
    *(SUBJECT_PREFIXES[kind] for kind in SUBJECT_PREFIXES if kind not in GRAPH_BOUND)
)

#: The file a case carries its reference facts in, beside ``model.json``.
FACTS_FILE = "facts.json"

#: Who drafts a facts file. One value, because every draft in this repository
#: is an agent's; the name is what ``reviewed_by`` may never equal.
DRAFTER: Final = "agent-stand-in"

#: What kind of gap a disputed value is, in the audit's own five classes:
#: a fact the source states outright and the model misses or misstates, an
#: inference the source supports but the model records as stated, a value the
#: source never settles, a representation the source supports as well as the
#: blessed one, and a value the source contradicts.
DisputeKind = Literal[
    "explicit-fact",
    "defensible-inference",
    "unknown",
    "alternate-representation",
    "error",
]

#: What a reviewer may rule on a dispute: change the model as proposed, or
#: keep it as it stands.
Ruling = Literal["change", "keep"]


class FactRow(BaseModel):
    """One reference fact, its rationale, and who has signed it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assertion: AssertionProposal
    #: Why the drafter read the source this way, in words a reviewer weighs.
    #: Read by a person, never by code, and required because a row nobody
    #: can argue with is a row nobody checked.
    rationale: str = Field(min_length=1)
    #: Who ruled that this row states what the source states. ``None`` until
    #: a person does.
    reviewed_by: str | None = None


class Dispute(BaseModel):
    """One blessed value the drafter reads the source as not establishing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    element_id: str = Field(min_length=1)
    attribute: str = Field(min_length=1)
    #: The value ``model.json`` holds now, so the entry stops matching the
    #: moment the model changes.
    blessed: str = Field(min_length=1)
    #: What the drafter proposes: a value, or the change to record.
    proposed: str = Field(min_length=1)
    kind: DisputeKind
    #: The source's words, or their absence, that the dispute rests on.
    basis: str = Field(min_length=1)
    ruling: Ruling | None = None
    reviewed_by: str | None = None


class SubjectAlias(BaseModel):
    """Other names a produced row may carry for one of the layer's own subjects.

    The counterpart of a case's element aliases, for a principal, a credential
    or an artifact: a subject the model names in words, whose ID is the slug
    of whatever it wrote. ``subject`` is the reference's own ID, and each of
    ``names`` slugs to another ID a reviewer rules names the same thing. A
    signed alias rewrites a produced row's subject, and a reference value
    that points at the subject, to the reference's spelling before the rows
    are compared; an unsigned one is a draft and rewrites nothing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(min_length=1)
    names: list[str] = Field(min_length=1)
    #: The produced subjects under which a *value* naming this subject is
    #: rewritten; empty means any. A reviewer who accepts "password" for the
    #: application account's password within that account's rows, and not on
    #: a shopper's, states the context here and the replay holds to it. A
    #: produced row whose own subject is the alias is rewritten regardless.
    within: list[str] = Field(default_factory=list)
    ruling: str = Field(min_length=1)
    reviewed_by: str | None = None

    @field_validator("subject")
    @classmethod
    def _own_subject(cls, value: str) -> str:
        prefix = value.split(":", 1)[0]
        if prefix in GRAPH_BOUND_PREFIXES:
            raise ValueError(
                f"{value!r} is an element; an element alias belongs in case.json"
            )
        if prefix not in OWN_PREFIXES:
            raise ValueError(f"{value!r} names no subject type")
        return value

    @property
    def subject_type(self) -> str:
        return self.subject.split(":", 1)[0]

    @property
    def alias_ids(self) -> list[str]:
        """The IDs the names slug to, through the resolver's own subject rule."""
        return [subject_id(self.subject_type, name) for name in self.names]


class QualifierAlias(BaseModel):
    """Other spellings a produced scope qualifier may carry for one reference value.

    A qualifier holds free text a model wrote, and its key is a digest of the
    normalized text, so ``read-write`` and ``read and write`` are two scopes.
    A signed alias rewrites the produced spelling to the reference's before
    the scopes are compared.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: QualifierKind
    value: str = Field(min_length=1)
    names: list[str] = Field(min_length=1)
    ruling: str = Field(min_length=1)
    reviewed_by: str | None = None


class ReferenceAliases(BaseModel):
    """The alias rulings a case's reference carries, for subjects and scopes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subjects: list[SubjectAlias] = Field(default_factory=list)
    qualifiers: list[QualifierAlias] = Field(default_factory=list)

    @property
    def entries(self) -> list[SubjectAlias | QualifierAlias]:
        return [*self.subjects, *self.qualifiers]


class ReferenceFacts(BaseModel):
    """One case's reference facts and its disputed values, as drafted or signed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case: str = Field(min_length=1)
    drafted_by: Literal["agent-stand-in"] = DRAFTER
    rows: list[FactRow] = Field(min_length=1)
    disputed: list[Dispute] = Field(default_factory=list)
    aliases: ReferenceAliases = Field(default_factory=ReferenceAliases)

    @property
    def unsigned_aliases(self) -> int:
        return sum(entry.reviewed_by is None for entry in self.aliases.entries)


def facts_path(case_dir: Path) -> Path:
    """Where this case's reference facts live, whether or not the file exists."""
    return case_dir / FACTS_FILE


def load_facts(case_dir: Path) -> ReferenceFacts:
    """This case's reference facts, validated, or a corpus error naming why not."""
    path = facts_path(case_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ReferenceFacts.model_validate(raw)
    except (OSError, ValueError, ValidationError) as exc:
        raise CorpusError(f"{path}: {exc}") from exc


def drafted_cases(corpus_dir: Path) -> list[Path]:
    """Every case directory carrying a facts file, in corpus order."""
    return sorted(
        path.parent for path in corpus_dir.glob(f"*/{FACTS_FILE}") if path.is_file()
    )


def reference_catalog(
    facts: ReferenceFacts, model: SystemModel, sources: Mapping[str, str]
) -> AssertionCatalog:
    """The rows resolved against the case's own blessed model and sources.

    Through the same resolver a produced proposal goes through, so a reference
    row that would not survive as a produced row cannot stand as a reference.
    Any refusal — a quote the source does not carry, a subject the model does
    not hold, a value the predicate does not admit — is a corpus error here,
    because a reference the gate would refuse measures nothing.

    ``sources`` is label to text, which is what the resolver reads; the caller
    supplies it so the corpus lint can pass the raw files it already holds
    rather than go through the harness loader it refuses to check the corpus
    through.
    """
    proposal = CatalogProposal(assertions=[row.assertion for row in facts.rows])
    catalog, issues = resolve_catalog(proposal, model, sources)
    issues = [*issues, *catalog_issues(catalog, model=model, sources=sources)]
    if issues:
        listed = "; ".join(
            f"row {issue.row}: {issue.code}: {issue.message}"
            if issue.row is not None
            else f"{issue.code}: {issue.message}"
            for issue in issues
        )
        raise CorpusError(f"{facts.case}: {FACTS_FILE} does not resolve: {listed}")
    return catalog
