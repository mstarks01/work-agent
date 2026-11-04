"""Controls: an **Owner**'s durable records of the protections the owner's
systems already have (ADRs 0123, 0124 and 0127).

A Control is one row and its evidence objects. The row holds the authored
:class:`ControlDocument` as JSON: the scope, the facts it proves, the rows that
name framework units and the list of evidence files. The text of each evidence
file is an object under :func:`~analysis_service.objects.evidence_key`, and the
document holds its digest.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal, Self, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from analysis_service.fact_answers import FactAnswer, key_ref, read_back
from analysis_service.frameworks import check_unit
from analysis_service.owner_files import text_refusal
from analysis_service.parsing import Sha256Hex
from analysis_service.sources import (
    MAX_LABEL_CHARS,
    EvidenceClass,
    plain_name,
    text_digest,
)
from analysis_service.system_model import ZONE_ATTRIBUTE, make_element_id

CONTROL_ID = r"^control-[0-9a-f]{32}$"
EVIDENCE_ID = r"^evidence-[0-9a-f]{32}$"
CONTROL_ID_PATTERN = re.compile(CONTROL_ID)
EVIDENCE_ID_PATTERN = re.compile(EVIDENCE_ID)

#: The most facts, rows and aliases one Control holds. Each bound is far over
#: what one protection states, and keeps one document small.
MAX_CONTROL_FACTS = 50
MAX_CONTROL_ROWS = 50
MAX_CONTROL_ALIASES = 20

#: What a Control protects: the whole application, one Trust Boundary by name,
#: or one Element of these types by name (ADR 0124). A flow is not a scope: its
#: identity is two endpoints and a label, so a name alone is not its ID.
ScopeType = Literal["application", "entity", "process", "store", "boundary"]

#: The kinds of fact a Control writes: a subject or an assertion is a question
#: of one job, so no Control writes one.
ControlFactKind = Literal["attribute", "question", "capability"]

#: The kinds of fact a Control of each scope writes (ADR 0125). A part takes
#: its attributes and the Question Kinds about it; the whole application takes
#: a Capability, which names no element. The document refuses another kind,
#: and the Controls page offers these.
SCOPE_FACT_KINDS: Mapping[ScopeType, frozenset[ControlFactKind]] = MappingProxyType(
    {
        "application": frozenset({"capability"}),
        "entity": frozenset({"attribute", "question"}),
        "process": frozenset({"attribute", "question"}),
        "store": frozenset({"attribute", "question"}),
        "boundary": frozenset({"attribute", "question"}),
    }
)

_PART_REFUSAL = (
    "A part takes its attributes and the questions about it. A capability is a"
    " fact of the whole application: protect the whole application for it."
)

#: Why a document is refused for a fact of another kind, by scope.
SCOPE_FACT_REFUSALS: Mapping[ScopeType, str] = MappingProxyType(
    {
        "application": (
            "The whole application takes only a capability. Choose the part that"
            " an attribute or a question is about."
        ),
        "entity": _PART_REFUSAL,
        "process": _PART_REFUSAL,
        "store": _PART_REFUSAL,
        "boundary": _PART_REFUSAL,
    }
)

_Name = Field(default="", max_length=MAX_LABEL_CHARS)


_T = TypeVar("_T")


def _repeated(values: Iterable[_T]) -> list[_T]:
    return [value for value, count in Counter(values).items() if count > 1]


class ControlScope(BaseModel):
    """What one Control protects, as the owner wrote it (ADR 0124)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ScopeType
    name: str = _Name
    aliases: list[str] = Field(default_factory=list, max_length=MAX_CONTROL_ALIASES)

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        return plain_name(value)

    @field_validator("aliases")
    @classmethod
    def _plain_aliases(cls, values: list[str]) -> list[str]:
        for value in values:
            if len(value) > MAX_LABEL_CHARS:
                raise ValueError(f"an alias is over {MAX_LABEL_CHARS} characters")
            plain_name(value)
        return values

    @model_validator(mode="after")
    def _one_shape(self) -> Self:
        if self.type == "application":
            if self.name or self.aliases:
                raise ValueError("The whole application takes no name and no alias.")
            return self
        if not self.name.strip():
            raise ValueError(
                "Name what the Control protects, or choose the whole application."
            )
        names = (self.name, *self.aliases)
        ids = []
        for name in names:
            try:
                ids.append(_scope_id(self.type, name))
            except ValueError:
                raise ValueError(f"“{name}” has no letter or digit.") from None
        for repeated in _repeated(ids):
            raise ValueError(
                f"The name “{names[ids.index(repeated)]}” appears twice in what the"
                " Control protects."
            )
        return self

    def name_id(self) -> str:
        """The element ID that the name gives, or ``""`` for the whole
        application."""
        return "" if self.type == "application" else _scope_id(self.type, self.name)

    def alias_ids(self) -> tuple[str, ...]:
        """The element ID that each alias gives, in the owner's order."""
        return tuple(_scope_id(self.type, alias) for alias in self.aliases)

    def element_ids(self) -> frozenset[str]:
        """The element ID that the name and each alias give, as a match reads
        them. Empty for the whole application."""
        if self.type == "application":
            return frozenset()
        return frozenset((self.name_id(), *self.alias_ids()))


def _scope_id(scope_type: ScopeType, name: str) -> str:
    """The element ID that ``name`` gives an element of ``scope_type``.
    **The one deriver of a scope's IDs**, which a match compares with the
    model's element IDs."""
    return make_element_id(scope_type, name)


class ControlRow(BaseModel):
    """One framework unit a Control covers, with the version of the package
    that offered it (ADR 0123). The scope supplies the element."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: A ``str`` and not a ``FrameworkName``: :func:`check_unit` names the
    #: refusal of a submission, and a row read back keeps a framework that a
    #: later build drops.
    framework: str = Field(max_length=32)
    unit: str = Field(max_length=32)
    version: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _a_carried_unit(self, info: ValidationInfo) -> Self:
        """Refuse a unit that the package does not carry. A row read back
        keeps its unit: a later catalog can drop it, and the offer at a
        submission compares the version."""
        if not read_back(info):
            check_unit(self.framework, self.unit)
        return self


class EvidenceFile(BaseModel):
    """One evidence file of a Control: everything but its text."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    id: str = Field(pattern=EVIDENCE_ID)
    name: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    evidence_class: EvidenceClass = Field(alias="class")
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Give the evidence file a name.")
        return plain_name(value)


class ControlDocument(BaseModel):
    """What the owner authored: the document that a Control's row holds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=CONTROL_ID)
    name: str = Field(max_length=MAX_LABEL_CHARS)
    scope: ControlScope
    facts: list[FactAnswer] = Field(max_length=MAX_CONTROL_FACTS)
    rows: list[ControlRow] = Field(max_length=MAX_CONTROL_ROWS)
    evidence: list[EvidenceFile]

    @field_validator("name")
    @classmethod
    def _a_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Give the Control a name.")
        return plain_name(value)

    @model_validator(mode="after")
    def _one_of_each(self) -> Self:
        if not self.facts and not self.rows:
            raise ValueError(
                "Add at least one fact or one row. A Control with neither changes"
                " nothing."
            )
        if any(key_ref(fact.key).element_id for fact in self.facts):
            raise ValueError("A fact of a Control names no element: the scope does.")
        refs = [key_ref(fact.key) for fact in self.facts]
        if ZONE_ATTRIBUTE in {ref.attribute for ref in refs}:
            raise ValueError(
                "A Control places no element, so it writes no trust_zone. Protect"
                " the zone instead."
            )
        for ref in refs:
            if len(ref.spellings) != 1:
                raise ValueError("A fact names one fact, in one spelling.")
            if ref.kind not in SCOPE_FACT_KINDS[self.scope.type]:
                raise ValueError(SCOPE_FACT_REFUSALS[self.scope.type])
        for key in _repeated(fact.key for fact in self.facts):
            ref = key_ref(key)
            name = (
                ref.attribute or ref.question or ref.capability or ref.assertion
            ) or ref.subject
            raise ValueError(
                f"The fact “{name}” appears twice. One Control writes one value for"
                " one fact."
            )
        for _, unit in _repeated((row.framework, row.unit) for row in self.rows):
            raise ValueError(f"The row {unit} appears twice.")
        for name in _repeated(file.name for file in self.evidence):
            raise ValueError(
                f"You already have an evidence file named “{name}” on this Control."
                " Give this file a different name."
            )
        for evidence_id in _repeated(file.id for file in self.evidence):
            raise ValueError(f"The evidence file {evidence_id} appears twice.")
        return self


class Control(BaseModel):
    """One Control as the store holds it: the owner, the time of the last
    save and the document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_id: str
    updated_at: datetime
    document: ControlDocument

    @classmethod
    def create(cls, *, owner_id: str, document: ControlDocument) -> Self:
        """The record of a save of ``document`` now."""
        return cls(owner_id=owner_id, updated_at=datetime.now(UTC), document=document)


#: What one :meth:`~analysis_service.jobs.JobStore.add_control` call did.
#: ``name_in_use``, ``alias_in_use`` and ``over_limit`` refuse the save, and
#: ``principal_exists`` refuses a new principal for an identity that holds one.
ControlAdmission = Literal[
    "added", "name_in_use", "alias_in_use", "over_limit", "principal_exists"
]

#: What one :meth:`~analysis_service.jobs.JobStore.replace_control` call did.
#: ``absent`` where the owner holds no Control of the ID, and ``stale`` where
#: a save changed the Control after the caller read it.
ControlReplacement = Literal[
    "replaced", "absent", "stale", "name_in_use", "alias_in_use"
]


def names_another_scope(scope: ControlScope, other: ControlScope) -> bool:
    """Whether an alias of ``scope`` or of ``other`` names the other scope.

    **The one rule of an alias across Controls** (ADR 0124). Two scopes of
    one type with one name are one scope, and two Controls may share it. Two
    scopes of one type with two names must give no element ID in common, or a
    match by that ID would point to both.
    """
    if scope.type != other.type or scope.name_id() == other.name_id():
        return False
    return bool(scope.element_ids() & other.element_ids())


def check_texts(
    document: ControlDocument,
    texts: Mapping[str, str],
    held: Sequence[EvidenceFile] = (),
) -> None:
    """Raise :class:`ValueError` where ``texts`` do not fit the evidence list of
    ``document``. ``held`` is the evidence that the store holds for the
    Control.

    A file that the store holds keeps its digest and its size, and takes no
    text. Each other file takes one text, whose digest and size it states.
    """
    kept = {file.id: file for file in held}
    for evidence_id in texts:
        if evidence_id in kept or all(
            file.id != evidence_id for file in document.evidence
        ):
            raise ValueError(f"evidence {evidence_id} is not a new file of the save")
    for file in document.evidence:
        if file.id in kept:
            stored = kept[file.id]
            if (file.sha256, file.size_bytes) != (stored.sha256, stored.size_bytes):
                raise ValueError(
                    f"evidence {file.id} holds another digest than its text"
                )
        elif file.id not in texts:
            raise ValueError(f"evidence {file.id} is new and has no text")
        elif (file.sha256, file.size_bytes) != (
            text_digest(texts[file.id]),
            len(texts[file.id].encode("utf-8")),
        ):
            raise ValueError(f"evidence {file.id} holds another digest than its text")


@dataclass(frozen=True)
class ControlLimits:
    """The deployment's bounds on an owner's Controls (``resilience.toml``)."""

    max_controls: int
    max_evidence: int
    max_evidence_bytes: int


@dataclass(frozen=True)
class EvidenceRefusal:
    """Why a save of a Control is refused for its evidence: the file at fault,
    or ``None`` where the count is at fault, and the message."""

    evidence_id: str | None
    message: str


#: The credential shapes the service knows, by the words a refusal names them
#: with. An evidence file that holds one is refused by line, and the service
#: redacts nothing (ADR 0127). An assignment counts only where it sets a
#: literal value: a quoted string that closes, a quoted or an unquoted run of
#: 256 characters, or an unquoted value at the end of its line that holds a
#: digit or a symbol. A quoted value does not count where it starts with a
#: reference or a space, where it is only a mask of ``*``, or where it joins a
#: name to another string, as ``"token=" + tok + "x"`` does. So a type name, a
#: masked value and a joined log string pass. An empty string does not count
#: either. Each start reads at most 257 characters of a value, so one scan of
#: a line stays linear.
CredentialShape = Literal[
    "a private key block",
    "a cloud access key ID",
    "a vendor token",
    "a password, secret or token set to a value",
]
_UNQUOTED = r"[^\s'\"#,;()\[\]{}<>]"
CREDENTIAL_SHAPES: Mapping[CredentialShape, re.Pattern[str]] = {
    "a private key block": re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
    ),
    "a cloud access key ID": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "a vendor token": re.compile(
        r"(?<![\w-])(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{36}"
        r"|github_pat_[A-Za-z0-9_]{22,}|xox[abposr]-[A-Za-z0-9-]{10,}"
        r"|glpat-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}|[rs]k_live_[A-Za-z0-9]{20,})"
    ),
    "a password, secret or token set to a value": re.compile(
        r"(?i)(?<![\w.-])[\w.-]*(?:password|passwd|secret|token)[\"']?\s*[:=]\s*"
        r"(?:\"(?![$<{%\"\s]|\*+\"|[,)+]\s{0,8}[\w.]{0,64}\s{0,8}(?:[,)+]|\"))"
        r"(?:[^\"\n]{1,256}\"|[^\"\n]{256})"
        r"|'(?![$<{%'\s]|\*+'|[,)+]\s{0,8}[\w.]{0,64}\s{0,8}(?:[,)+]|'))"
        r"(?:[^'\n]{1,256}'|[^'\n]{256})"
        rf"|(?![$<{{%*])(?:(?={_UNQUOTED}{{0,255}}[\d!@^&*+~?/\\|]){_UNQUOTED}{{1,256}}\s*$"
        rf"|{_UNQUOTED}{{256}}))"
    ),
}


def credential_hit(text: str) -> tuple[int, CredentialShape] | None:
    """The number of the first line of ``text`` that holds a credential shape,
    and the shape, or ``None``."""
    for number, line in enumerate(text.split("\n"), start=1):
        for shape, pattern in CREDENTIAL_SHAPES.items():
            if pattern.search(line):
                return number, shape
    return None


def control_refusal(
    document: ControlDocument, texts: Mapping[str, str], *, limits: ControlLimits
) -> EvidenceRefusal | None:
    """Why the evidence of a save of ``document`` is refused, or ``None``.

    **The one rule of evidence content.** ``texts`` holds the text of each new
    file by its ID. The count comes first. Each new text then passes, in the
    document's order, the Owner File text rules (strict UTF-8, the size, no
    control character, some text) and the credential table.
    """
    if len(document.evidence) > limits.max_evidence:
        return EvidenceRefusal(
            None,
            f"A Control holds at most {limits.max_evidence} evidence files. Remove a"
            " file before you add another.",
        )
    for file in document.evidence:
        if file.id in texts:
            message = _text_refusal(file.name, texts[file.id], limits)
            if message is not None:
                return EvidenceRefusal(file.id, message)
    return None


def evidence_too_large(max_bytes: int) -> str:
    """The refusal of an evidence file over ``max_bytes``."""
    return (
        f"This file is larger than the {max_bytes:,} bytes one evidence file can hold."
    )


def _text_refusal(name: str, text: str, limits: ControlLimits) -> str | None:
    refusal = text_refusal(
        text,
        max_bytes=limits.max_evidence_bytes,
        too_large=evidence_too_large(limits.max_evidence_bytes),
    )
    if refusal is not None:
        return refusal
    hit = credential_hit(text)
    if hit is None:
        return None
    number, shape = hit
    return (
        f"Line {number} of “{name}” holds {shape}. Remove it, then add the file"
        " again: the service stores no credential."
    )
