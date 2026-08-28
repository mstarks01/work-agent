"""``ReferenceClaim``, its STRIDE narrowing, and the loader for the golden corpus.

``ReferenceClaim`` is an eval-side model, deliberately **not**
:class:`~stride_service.report.Claim`. A produced claim carries fields that must
not be graded — a 4000-character ``description`` nobody asked the model to
reproduce verbatim — and lacks ``tier``, the field that makes a recall threshold
mean anything. Its ``id`` would be actively misleading: a reference ``S-01`` and
a produced ``S-01`` are the same string for no reason at all.

It layers the way the service's own records do (:class:`ReferenceClaim` ->
:class:`ReferenceThreat`), and for the same reason: what every framework's
reference set has in common is a claim string, a tier and the elements it is
about, while the category and the two rated severity axes are STRIDE's. A
framework whose records cite no element is expressible on the base, which is
what stops the neutral half of the harness from assuming one.

What *is* shared with the service is imported rather than restated —
``StrideCategory``, ``Rating`` and the severity matrix — so the corpus cannot
drift from the shipped vocabulary.

**One corpus, split by framework inside each case.** ``source.md``,
``model.json``, ``corrections.md`` and ``case.json`` are shared and single,
because #162 ruled that one extraction serves every framework and the blessed
model is the one artifact two frameworks must agree on. A second corpus tree
would copy twelve sources and twelve blessed models, and two copies of one
blessed model rot apart. The records split instead: ``claims/<framework>.json``,
one file per framework the case carries records for.

Loading fails closed in the shape
:class:`~stride_service.markdown_loader.MarkdownLoader` established — a missing
file, a malformed case, a model that fails the shipped validity gate, or a
reference citing an element the blessed model does not contain raises
:class:`CorpusError`. The last of those mirrors the exemplar lint: a reference
claim pointing at a nonexistent element is unscoreable, and silently dropping it
would quietly lower the recall denominator.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from evals.harness.verbs import check_verb
from stride_service.frameworks.asvs.record import AsvsChapter
from stride_service.frameworks.stride.record import StrideCategory
from stride_service.report import (
    FrameworkName,
    Rating,
    SeverityLevel,
    derive_severity_level,
)
from stride_service.sources import MAX_LABEL_CHARS, Source, SourceKind
from stride_service.system_model import SystemModel
from stride_service.validation import parse_and_validate

# Two tiers, because one weight makes every threshold wrong. ``must-find``
# drives the hard recall gate, ``expected`` a tracked, softer number.
#
# **The harness's, never a package's.** What a working tool must find on this
# corpus is a statement about the corpus; no package declares one, and an
# option that selects which claims are in play (an ASVS level, say) is an input
# rather than a tier.
Tier = Literal["must-find", "expected"]

#: The shared files every case carries, whatever frameworks it is graded for.
CASE_FILES = ("source.md", "model.json", "case.json")

#: Where one framework's reference records live inside a case.
CLAIMS_DIR = "claims"


class CorpusError(ValueError):
    """A golden case is missing, malformed, or internally inconsistent."""


class ReferenceSeverity(BaseModel):
    """The recorded severity for a reference threat: the two rated axes only.

    The band is derived by the shipped matrix exactly as production derives
    it, so severity calibration compares like with like and is pure arithmetic.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    likelihood: Rating
    impact: Rating

    @property
    def level(self) -> SeverityLevel:
        return derive_severity_level(self.likelihood, self.impact)


class ReferenceClaim(BaseModel):
    """One claim the corpus asserts a working tool must (or should) report.

    Agent-authored and unreviewed, like everything under ``evals/`` —
    ``evals/README.md`` states the provenance once for the whole directory.

    The neutral half: a claim string to match on, the tier that weights it, the
    elements it is about, and rationale that is never scored. Everything a
    framework grades *with* — a category, a severity — narrows this.

    ``affected_element_ids`` may be empty here where STRIDE's narrowing requires
    one. A framework whose records are requirement-shaped may legitimately cite
    no element, and element accuracy counts over the records that cite one
    rather than failing the ones that do not.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim: str = Field(min_length=1, max_length=500)
    tier: Tier
    affected_element_ids: tuple[str, ...] = ()
    notes: str = ""  # the author's rationale, never scored
    #: The attacker action, from :mod:`evals.harness.verbs`. Assigned as the
    #: claim is written (``BLESSING.md`` step 4b) rather than derived later,
    #: because deriving it re-runs the author's decision with less context.
    #: ``None`` where nobody has assigned one, which
    #: ``tests/test_verb_coverage.py`` counts per case. It is what fingerprint
    #: version 2 reads, so the count reaching zero is what lets the default move.
    verb: str | None = None

    @field_validator("verb")
    @classmethod
    def _known_verb(cls, value: str | None) -> str | None:
        """An unrecognised verb fails here rather than matching nothing later."""
        if value is not None:
            check_verb(value)
        return value

    @property
    def must_find(self) -> bool:
        return self.tier == "must-find"

    @property
    def lane(self) -> str:
        """Which of its framework's lanes this claim belongs to.

        Declared on the base and answered by each record, so a scorer reading a
        lane never spells ``category`` or ``chapter`` — the same separation
        :attr:`~stride_service.frameworks.IdRule.lane_field` makes on the
        service side. A framework whose records carry no lane answers the empty
        string, which is legal rather than a defect.
        """
        return ""


class ReferenceThreat(ReferenceClaim):
    """STRIDE's reference record: a category and a graded severity.

    ``affected_element_ids`` is narrowed to non-empty for the same reason
    :class:`~stride_service.frameworks.stride.record.DraftThreat` narrows it:
    every STRIDE finding is about something in the graph, so a reference threat
    naming no element is unscoreable rather than a legal shape.
    """

    category: StrideCategory
    affected_element_ids: tuple[str, ...] = Field(min_length=1)

    @property
    def lane(self) -> str:
        return self.category

    severity: ReferenceSeverity


class ReferenceRequirement(ReferenceClaim):
    """ASVS's reference record: the chapter and the requirement it expects.

    ``requirement`` is the standard's own identifier, ``V1.2.4``. It is what
    makes this reference set **closed** where STRIDE's is open: the catalog is
    finite, so a case names the requirements it expects a ruling on and a scorer
    can derive the rest.

    ``affected_element_ids`` keeps the neutral empty default. Most ASVS
    requirements address a coding practice with no position in the graph, so a
    reference record naming no element is the ordinary case here.

    No severity and no verdict. This package grades nothing, and what a ruling
    should conclude belongs in ``claim`` as the sentence a scorer matches on —
    adding a verdict field would put a second, unscored copy of it beside the
    first.
    """

    chapter: AsvsChapter
    requirement: str = Field(pattern=r"^V\d{1,2}\.\d{1,2}\.\d{1,2}$")

    @property
    def lane(self) -> str:
        return self.chapter


#: The reference record each framework's corpus file validates as. Harness data
#: keyed off the closed :data:`~stride_service.report.FrameworkName`, not a
#: tenth package member: what a reference set looks like is the *eval's*
#: business, and a package that shipped its own would be asserting how well it
#: must be measured.
REFERENCE_TYPES: Mapping[FrameworkName, type[ReferenceClaim]] = MappingProxyType(
    {"asvs": ReferenceRequirement, "stride": ReferenceThreat}
)


class CaseFramework(BaseModel):
    """One framework this case is graded for, declared as a job submits it.

    Three facts ride here rather than in three places:

    ``name``
        Which frameworks the case carries records for. A case a framework's
        **Precondition** refutes declares nothing here, carries no record file,
        and is reported *unexercised* rather than as zero recall — a missing
        file and a refused framework are different facts, and a recall
        denominator cannot tell them apart on its own.
    ``options``
        The package's own job-level values. An option that changes which claims
        are in play changes the reference set, so it travels with the reference
        set rather than on the command line; ``--framework`` stays a pure
        selection and names no option at all.
    ``exemplar_proximity``
        Whether this case is near the architecture *this package's* exemplars
        are written in. It sits on the (case, framework) pair because exemplars
        live at ``frameworks/<name>/lanes/<lane>/exemplars.md`` — case ``01`` is
        near STRIDE's payments exemplar and near nothing else.

        A bit, not a scale, and it stays a bit however many exemplar systems
        there are: what the delta asks is whether recall depends on having been
        shown the architecture, and that question has two sides no matter how
        wide the near side gets. A framework whose cases declare no ``near``
        case reports the delta unexercised rather than zero.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: FrameworkName
    options: Mapping[str, object] = Field(default_factory=dict)
    exemplar_proximity: Literal["near", "far"]


class CaseSource(BaseModel):
    """One declared input file, and what it is.

    The corpus declares its sources the way a job submits them, so the harness
    reads the label from here rather than hard-coding one at each seed site —
    which is also what ties a case's ``source_label`` values to something a
    lint can check.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SourceKind
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    file: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReadRecord(BaseModel):
    """One file a sitting read, pinned to the bytes that were read.

    The digest is what makes staleness mechanical (#327): a later PR that
    edits a read file no longer matches, so the case goes back on the list
    fail-closed in the PR that caused it — a person re-reads, or names the
    case as unread by putting it back in ``UNREVIEWED``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CaseSitting(BaseModel):
    """One **Case Sitting**: who read this case, when, which bytes, and the evidence.

    ``evals/BLESSING.md`` step 6 is one reading session over ``source.md``, the
    model and every reference set together. Until an entry exists on a case,
    nobody has done it — and the corpus shipped 13 cases in that state, which is
    how a reference claim asserting a fact its own model does not hold survived
    to review sitting 01. ``tests/test_case_review.py`` names every case still
    waiting and fails on a new one that arrives without an entry.

    ``reviewer`` is the GitHub login of the account whose PR carries the
    sitting — the same binding a vote uses (#320), checked by CI. ``document``
    names the filled ``REVIEW-<login>.md`` committed beside the case, because
    only the filled copy shows the method ran; the generated ``REVIEW.md``
    stays derived and unfilled.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The GitHub login shape, as ledger.GITHUB_LOGIN spells it — but pydantic's
    #: regex engine has no look-ahead, so the length rides on ``max_length``.
    reviewer: str = Field(pattern=r"^[A-Za-z0-9](?:-?[A-Za-z0-9])*$", max_length=39)
    #: ISO date. A sitting is a dated event; the reference set moves under it.
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    read: list[ReadRecord] = Field(min_length=1)
    document: str = Field(min_length=1)
    notes: str = ""


class CaseMetadata(BaseModel):
    """``case.json``: what the case is, where it came from, and who grades it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    bootstrap: str = Field(min_length=1)
    sources: list[CaseSource] = Field(min_length=1)
    # The aggregate, taken over the refs exactly as a report's InputRef is.
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Non-empty: a case no framework grades is a case that scores nothing, and
    # a corpus quietly carrying one lowers no denominator visibly.
    frameworks: list[CaseFramework] = Field(min_length=1)
    #: Every Case Sitting this case has had, oldest first, append-only — a
    #: re-read is a new entry, never an edit (#327). Empty until a person
    #: reads the case: the 13 cases that shipped unread are real, and a
    #: required entry would make them unloadable rather than visibly
    #: unreviewed.
    reviews: list[CaseSitting] = Field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class GoldenCase:
    """One case: the submitted text, the blessed model, the reference sets.

    Two artifacts rather than an input/output pair, which is what buys the
    three eval modes and per-node attribution of a regression.

    ``references`` is keyed by framework because the records are: one shared
    model, N reference sets, exactly as one shared model carries N analysis
    blocks in the report.
    """

    meta: CaseMetadata
    sources: tuple[Source, ...]
    model: SystemModel
    references: Mapping[FrameworkName, tuple[ReferenceClaim, ...]]

    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def frameworks(self) -> tuple[FrameworkName, ...]:
        """The frameworks this case is graded for, in declared order."""
        return tuple(declared.name for declared in self.meta.frameworks)

    def declaration(self, framework: FrameworkName) -> CaseFramework:
        """This case's declaration for one framework, or a corpus error."""
        for declared in self.meta.frameworks:
            if declared.name == framework:
                return declared
        raise CorpusError(f"{self.id}: carries no records for {framework!r}")

    def claims_for(self, framework: FrameworkName) -> tuple[ReferenceClaim, ...]:
        """One framework's reference set."""
        return self.references[framework]

    def must_find_for(self, framework: FrameworkName) -> tuple[ReferenceClaim, ...]:
        """One framework's ``must-find`` records."""
        return tuple(ref for ref in self.references[framework] if ref.must_find)

    def stride_claims(self) -> tuple[ReferenceThreat, ...]:
        """This case's STRIDE reference set, at the record type it validates as.

        :meth:`claims_for` is typed at the neutral base because it serves every
        framework, while STRIDE's scorers grade ``category`` and ``severity`` —
        fields only the narrowed record carries. :func:`load_case` already
        validates each framework's file against its own type from
        :data:`REFERENCE_TYPES`, so this re-states that fact where a caller
        needs it rather than casting it away, and fails loudly if it ever stops
        being true.
        """
        claims = self.references["stride"]
        narrowed = tuple(ref for ref in claims if isinstance(ref, ReferenceThreat))
        if len(narrowed) != len(claims):
            raise CorpusError(
                f"{self.id}: stride references did not load as ReferenceThreat"
            )
        return narrowed

    @property
    def source_text(self) -> str:
        """Every source's text, for the scorers that read the input as prose."""
        return "\n\n".join(source.text for source in self.sources)


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

    Lane agents only ever see valid models in production; a case whose blessed
    model would be rejected there cannot ground a score here.
    """
    model, issues = parse_and_validate(_read_json(case_dir / "model.json"))
    if model is None or issues:
        detail = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        raise CorpusError(f"{case_dir.name}: model.json is not valid: {detail}")
    return model


def claims_path(case_dir: Path | str, framework: FrameworkName) -> Path:
    """Where one framework's reference records live for one case."""
    return Path(case_dir) / CLAIMS_DIR / f"{framework}.json"


def _load_references(
    case_dir: Path, framework: FrameworkName, model: SystemModel
) -> tuple[ReferenceClaim, ...]:
    path = claims_path(case_dir, framework)
    relative = f"{CLAIMS_DIR}/{framework}.json"
    if not path.is_file():
        raise CorpusError(
            f"{case_dir.name}: case.json declares {framework!r}, but"
            f" {relative} does not exist"
        )
    raw = _read_json(path)
    if not isinstance(raw, list) or not raw:
        raise CorpusError(f"{case_dir.name}: {relative} must be a non-empty list")

    record = REFERENCE_TYPES[framework]
    try:
        references = tuple(record.model_validate(item) for item in raw)
    except ValidationError as exc:
        raise CorpusError(f"{case_dir.name}: {relative}: {exc}") from exc

    known_ids = {element.id for element in model.elements()}
    dangling = [
        f"reference {index} cites {element_id!r}"
        for index, reference in enumerate(references)
        for element_id in reference.affected_element_ids
        if element_id not in known_ids
    ]
    if dangling:
        raise CorpusError(
            f"{case_dir.name}: {relative} references elements absent from"
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
    repeated = sorted(
        {
            declared.name
            for declared in meta.frameworks
            if [d.name for d in meta.frameworks].count(declared.name) > 1
        }
    )
    if repeated:
        raise CorpusError(
            f"{case_dir.name}: case.json declares {', '.join(repeated)} more than"
            " once; a framework has one reference set per case"
        )

    model = _load_model(case_dir)
    return GoldenCase(
        meta=meta,
        sources=_load_sources(case_dir, meta),
        model=model,
        references=MappingProxyType(
            {
                declared.name: _load_references(case_dir, declared.name, model)
                for declared in meta.frameworks
            }
        ),
    )


def _load_sources(case_dir: Path, meta: CaseMetadata) -> tuple[Source, ...]:
    """The case's declared sources, in declared order.

    A declared file that is missing is a corpus error rather than an empty
    source: a case that silently analyses less text than it claims would score
    against a reference set written for the whole of it.
    """
    sources = []
    for declared in meta.sources:
        path = case_dir / declared.file
        if not path.is_file():
            raise CorpusError(
                f"{case_dir.name}: case.json declares {declared.file!r}, which"
                " does not exist"
            )
        sources.append(
            Source(
                kind=declared.kind,
                label=declared.label,
                text=path.read_text(encoding="utf-8"),
            )
        )
    return tuple(sources)


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
