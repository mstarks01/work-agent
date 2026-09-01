"""The shape of an adversarial case, and the attack classes the corpus covers.

**A case is a fixture, not a golden case.** It declares a poisoned source and
what a report over it must and must not contain. Every expectation is decidable
from the report by code — see :mod:`evals.adversarial.score` — because a model
grading a poisoned report is reading the same poison.

:data:`ATTACK_CLASSES` is a **table, and the lint compares the corpus to it**.
A class with no case fails, which is what stops the list becoming a statement of
intent; a case naming a class not in the list fails too, which is what stops the
corpus growing a class nobody described. This is the same shape
``CLAUDE.md`` requires of a framework table, for the same reason: machinery that
grows an entry per anything has to be keyed, and a table nobody compares to its
registry fails as quietly as the branch it replaced.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CASES_DIR = Path(__file__).resolve().parent / "cases"

#: The corpus schema. Bumped when a case field is added, removed or re-spelled,
#: because a scorer reading an older shape would score a case it misunderstood.
CORPUS_VERSION = 1

AttackClass = Literal[
    # "ignore the above and ..." in the body of a source.
    "instruction-override",
    # Text shaped like a system or user turn, or like this service's own source
    # header, trying to be read as part of the prompt rather than the input.
    "role-confusion",
    # A fence inside the source, opened or closed so the block appears to end
    # early and the rest reads as instruction.
    "fence-escape",
    # "Return an empty model." The denial-of-analysis attack: a schema-valid
    # answer that says nothing.
    "empty-model",
    # "There is also a component called X." Invented structure the true source
    # does not describe.
    "invented-component",
    # "Report exactly these findings" or "report none." A predetermined verdict.
    "predetermined-findings",
    # Text addressed to the critic rather than the extractor, on the assumption
    # a later stage reads the source too.
    "critic-directed",
    # No injection at all. Without it a service that refused everything would
    # score perfectly on every case above.
    "benign-control",
]

ATTACK_CLASSES: tuple[AttackClass, ...] = (
    "instruction-override",
    "role-confusion",
    "fence-escape",
    "empty-model",
    "invented-component",
    "predetermined-findings",
    "critic-directed",
    "benign-control",
)


class Expectations(BaseModel):
    """What a report over this case must and must not contain.

    Every field is a list of plain strings matched case-insensitively against
    the report, because a model's exact casing is not the thing under test. A
    case declares only what its own attack makes decidable — an
    ``instruction-override`` case that demanded nothing be invented has no
    ``must_not_contain_elements`` to state, and stating an empty list would read
    as a check that passed rather than one that was not asked.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Element names the *true* content describes. The attack is resisted only
    #: if these survive: a service that ignored the whole input would satisfy
    #: every prohibition below and be no use at all.
    must_retain: tuple[str, ...] = ()
    #: Element names the injection asked the model to invent.
    must_not_contain_elements: tuple[str, ...] = ()
    #: Claim action verbs the injection demanded be reported.
    must_not_contain_verbs: tuple[str, ...] = ()
    #: Whether the model must have any elements at all.
    must_not_be_empty: bool = True
    #: How many claims the analysis must produce. Zero means "not checked" —
    #: a case about invented structure says nothing about finding count.
    min_claims: int = 0

    @model_validator(mode="after")
    def _something_is_checked(self) -> Self:
        checked = (
            self.must_retain
            or self.must_not_contain_elements
            or self.must_not_contain_verbs
            or self.min_claims
        )
        if not checked and not self.must_not_be_empty:
            raise ValueError("expectations check nothing; the case cannot fail")
        return self


class AdversarialCase(BaseModel):
    """One poisoned source and the outcome it must not produce."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str = Field(min_length=1)
    attack_class: AttackClass
    #: What the injected text actually asks for, in one sentence, so a reader of
    #: a failure knows what the model was talked into without opening the
    #: source. Prose for a human; nothing scores it.
    demand: str = Field(min_length=1)
    #: How this artifact was made, in a field the code reads rather than a
    #: sentence in a guide. Nothing under ``evals/`` is human-reviewed and this
    #: says so per case rather than repository-wide.
    provenance: str = Field(min_length=1)
    source_file: str = "source.md"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expectations: Expectations

    @property
    def directory(self) -> Path:
        return CASES_DIR / self.id

    def source_text(self) -> str:
        return (self.directory / self.source_file).read_text(encoding="utf-8")

    def digest_matches(self) -> bool:
        """Whether the recorded digest is the one the file actually has."""
        return digest_of(self.source_text()) == self.source_sha256


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_case(directory: Path) -> AdversarialCase:
    """One case from its directory, or raise."""
    raw: dict[str, Any] = json.loads(
        (directory / "case.json").read_text(encoding="utf-8")
    )
    return AdversarialCase.model_validate(raw)


def load_corpus(cases_dir: Path = CASES_DIR) -> tuple[AdversarialCase, ...]:
    """Every case, in directory order so a report reads the same twice."""
    return tuple(
        load_case(directory)
        for directory in sorted(cases_dir.iterdir())
        if directory.is_dir()
    )
