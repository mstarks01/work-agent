"""``ReferenceThreat`` and the loader for the golden corpus.

``ReferenceThreat`` is an eval-side model, deliberately **not**
:class:`~stride_service.report.DraftThreat`. A draft carries fields that must
not be graded — ``mitigations``, and a 4000-character ``description`` nobody
asked the model to reproduce verbatim — and lacks ``tier``, the field that
makes a recall threshold mean anything. Its ``id`` would be actively
misleading: a reference ``S-01`` and a produced ``S-01`` are the same string
for no reason at all.

What *is* shared is imported rather than restated: ``StrideCategory``,
``Rating`` and the severity matrix come from
:mod:`stride_service.report`, so the corpus cannot drift from the shipped
vocabulary.

Loading fails closed in the shape
:class:`~stride_service.markdown_loader.MarkdownLoader` established — a missing
file, a malformed case, a model that fails the shipped validity gate, or a
reference citing an element the blessed model does not contain raises
:class:`CorpusError`. The last of those mirrors the exemplar lint: a reference
threat pointing at a nonexistent element is unscoreable, and silently dropping
it would quietly lower the recall denominator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stride_service.report import (
    Rating,
    SeverityLevel,
    StrideCategory,
    derive_severity_level,
)
from stride_service.sources import Source
from stride_service.system_model import SystemModel
from stride_service.validation import parse_and_validate

# Two tiers, because one weight makes every threshold wrong. ``must-find``
# drives the hard recall gate, ``expected`` a tracked, softer number.
Tier = Literal["must-find", "expected"]

CASE_FILES = ("source.md", "model.json", "threats.json", "case.json")


class CorpusError(ValueError):
    """A golden case is missing, malformed, or internally inconsistent."""


class ReferenceSeverity(BaseModel):
    """The SME's severity for a reference threat: the two rated axes only.

    The band is derived by the shipped matrix exactly as production derives
    it, so severity calibration compares like with like and needs no judge.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    likelihood: Rating
    impact: Rating

    @property
    def level(self) -> SeverityLevel:
        return derive_severity_level(self.likelihood, self.impact)


class ReferenceThreat(BaseModel):
    """One threat the SME says a working tool must (or should) report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: StrideCategory
    affected_element_ids: tuple[str, ...] = Field(min_length=1)
    claim: str = Field(min_length=1, max_length=500)
    tier: Tier
    severity: ReferenceSeverity
    notes: str = ""  # SME rationale, never scored

    @property
    def must_find(self) -> bool:
        return self.tier == "must-find"


class CaseMetadata(BaseModel):
    """``case.json``: what the case is and where it came from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    # The near/far split is the whole instrument for the exemplar-domain-bias
    # delta, so it is typed, not a free string.
    exemplar_proximity: Literal["near", "far"]
    provenance: str = Field(min_length=1)
    bootstrap: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    notes: str = ""


@dataclass(frozen=True)
class GoldenCase:
    """One case: the submitted text, the blessed model, the reference set.

    Two artifacts rather than an input/output pair, which is what buys the
    three eval modes and per-node attribution of a regression.
    """

    meta: CaseMetadata
    source_text: str
    model: SystemModel
    references: tuple[ReferenceThreat, ...]

    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def sources(self) -> tuple[Source, ...]:
        """The case's input, in the shape every driver of the graph takes."""
        return (Source.description(self.source_text),)

    @property
    def must_find(self) -> tuple[ReferenceThreat, ...]:
        return tuple(ref for ref in self.references if ref.must_find)


def _read_json(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise CorpusError(f"{path}: cannot be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"{path}: invalid JSON: {exc}") from exc


def _load_model(case_dir: Path) -> SystemModel:
    """The blessed model, put through the shipped validity gate.

    Analysts only ever see valid models in production; a case whose blessed
    model would be rejected there cannot ground a score here.
    """
    model, issues = parse_and_validate(_read_json(case_dir / "model.json"))
    if model is None or issues:
        detail = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        raise CorpusError(f"{case_dir.name}: model.json is not valid: {detail}")
    return model


def _load_references(
    case_dir: Path, model: SystemModel
) -> tuple[ReferenceThreat, ...]:
    raw = _read_json(case_dir / "threats.json")
    if not isinstance(raw, list) or not raw:
        raise CorpusError(f"{case_dir.name}: threats.json must be a non-empty list")

    try:
        references = tuple(ReferenceThreat.model_validate(item) for item in raw)
    except ValidationError as exc:
        raise CorpusError(f"{case_dir.name}: threats.json: {exc}") from exc

    known_ids = {element.id for element in model.elements()}
    dangling = [
        f"reference {index} cites {element_id!r}"
        for index, reference in enumerate(references)
        for element_id in reference.affected_element_ids
        if element_id not in known_ids
    ]
    if dangling:
        raise CorpusError(
            f"{case_dir.name}: threats.json references elements absent from"
            f" model.json: {'; '.join(dangling)}"
        )
    return references


def load_case(case_dir: Path | str) -> GoldenCase:
    """Load and check one golden case directory."""
    case_dir = Path(case_dir)
    missing = [name for name in CASE_FILES if not (case_dir / name).is_file()]
    if missing:
        raise CorpusError(f"{case_dir}: missing {', '.join(missing)}")

    try:
        meta = CaseMetadata.model_validate(_read_json(case_dir / "case.json"))
    except ValidationError as exc:
        raise CorpusError(f"{case_dir.name}: case.json: {exc}") from exc
    if meta.id != case_dir.name:
        raise CorpusError(
            f"{case_dir.name}: case.json id {meta.id!r} does not match the"
            " directory name"
        )

    model = _load_model(case_dir)
    return GoldenCase(
        meta=meta,
        source_text=(case_dir / "source.md").read_text(encoding="utf-8"),
        model=model,
        references=_load_references(case_dir, model),
    )


def load_corpus(corpus_dir: Path | str) -> tuple[GoldenCase, ...]:
    """Every case in the corpus, in stable numeric-prefix order."""
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.is_dir():
        raise CorpusError(f"corpus root is not a directory: {corpus_dir}")
    cases = tuple(
        load_case(path) for path in sorted(corpus_dir.iterdir()) if path.is_dir()
    )
    if not cases:
        raise CorpusError(f"no cases under {corpus_dir}")
    return cases
