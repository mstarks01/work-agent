"""What an end-to-end run lost before its lanes ran, read against the analysis-mode run beside it.

Every scored sweep runs in ``analysis`` mode, with the blessed model injected
at prepare, so no published number has ever included an extraction failure
(#742). The extraction score measures that stage on its own, against the same
blessed model, and nothing joins the two: a reference an end-to-end run misses
could have been lost at extraction or downstream of it, and the two artifacts
side by side do not say which.

This instrument reads one end-to-end run and one analysis-mode run of the same
corpus on the same coordinates and gives every reference one of four fates:

* ``both``: matched in both runs. Extraction did not cost it.
* ``downstream``: missed in both. The lanes lose it on the blessed model too,
  so :mod:`evals.harness.losses` already charges it to a cause.
* ``extraction``: matched on the blessed model and missed end to end. The
  only difference between the two runs is the model the lanes read, so this
  is the reference extraction cost — up to the run-to-run band, which the
  analysis-mode spread measures and this does not.
* ``recovered``: missed on the blessed model and matched end to end. Noise,
  or an extraction that read the source better than the corpus did; either
  way a row to read, never a gain to claim.

For an ``extraction`` fate on a package that composes its identity from an
action and a place — read off :data:`~evals.harness.fingerprint.IDENTIFIER_OF`,
never off a name, so a package added tomorrow answers by its own declaration —
the row says what the extracted model lacked at that place, read through
:func:`evals.harness.modes.score_extraction` over the end-to-end report's own
embedded model, so the same reader that scores an extraction sweep scores this
one: the reference's elements absent from the model, and the scored
attributes that differ on the elements it did carry. A row with neither is
the sharper finding — the lane lost on a model that held the place — and it
is counted as its own kind rather than folded into either.

Two runs of two modes is the one comparison :func:`evals.harness.stability.comparability_warnings`
warns about that this instrument exists to make, so that warning alone is
consumed here and every other one is printed, because a changed model or an
unshared case changes what the fates mean.

Nothing here needs a provider. It reads two finished artifacts, one report
bundle and the corpus, so it costs nothing after the paired run that #742
asks for; the tests drive it over synthetic pairs and the real case 01.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from analysis_service.claims import FrameworkName
from analysis_service.system_model import SystemModel
from evals.harness.bundle import reports_dir
from evals.harness.fingerprint import IDENTIFIER_OF
from evals.harness.modes import ExtractionResult, ExtractionScore, score_extraction
from evals.harness.provenance import ProvenanceError
from evals.harness.reference import GoldenCase
from evals.harness.stability import (
    Scope,
    ScoredRun,
    comparability_warnings,
    refuse_incomparable,
)

Fate = Literal["both", "downstream", "extraction", "recovered"]
FATES: tuple[Fate, ...] = ("both", "downstream", "extraction", "recovered")

#: The mode each side of the pair has to be, by position.
END_TO_END = "end-to-end"
ANALYSIS = "analysis"

#: The one comparability warning this instrument consumes rather than prints:
#: the two modes are the question, not a caveat on the answer.
_MODE_WARNING_PREFIX = "runs are of different modes"


@dataclass(frozen=True)
class ReferenceFate:
    """One reference across the pair, and what the extracted model lacked where it lost."""

    reference: str
    fate: Fate
    #: ``None`` for a package whose references carry no tier.
    must_find: bool | None
    #: The reference's elements the extracted model does not carry. Only on an
    #: ``extraction`` fate of a package whose references name elements.
    missing_elements: tuple[str, ...] = ()
    #: ``<element>.<attribute>: <blessed> -> <extracted>`` for every scored
    #: attribute that differs on a reference element the model did carry.
    differing_attributes: tuple[str, ...] = ()

    @property
    def held_the_place(self) -> bool:
        """An ``extraction`` loss on a model that carried every element the reference names, unchanged."""
        return (
            self.fate == "extraction"
            and not self.missing_elements
            and not self.differing_attributes
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "fate": self.fate,
            "must_find": self.must_find,
            "missing_elements": list(self.missing_elements),
            "differing_attributes": list(self.differing_attributes),
            "held_the_place": self.held_the_place,
        }


@dataclass(frozen=True)
class CaseHandoff:
    """One ``(framework, case)`` pair: every reference's fate, and the extraction behind them."""

    framework: str
    case: str
    fates: tuple[ReferenceFate, ...]
    #: The end-to-end model scored against the blessed one, where the package
    #: names elements and the report was found. ``None`` for a package whose
    #: references are catalog identifiers, which map to no element.
    extraction: ExtractionScore | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    #: References missed in both runs that no row names, because a package
    #: identified by a catalog scores its matched identifiers and a count, so
    #: the ones missed twice are a number and never a list. Zero for a package
    #: whose references are indexed.
    unnamed_downstream: int = 0

    @property
    def by_fate(self) -> dict[str, int]:
        counts = Counter(entry.fate for entry in self.fates)
        counts["downstream"] += self.unnamed_downstream
        return {fate: counts[fate] for fate in FATES}

    def to_json(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "case": self.case,
            "references": len(self.fates) + self.unnamed_downstream,
            "by_fate": self.by_fate,
            "crossings_match": (
                self.extraction.crossings_match if self.extraction else None
            ),
            "missing_elements": (
                list(self.extraction.missing) if self.extraction else []
            ),
            "fates": [entry.to_json() for entry in self.fates],
            "warnings": list(self.warnings),
        }


def names_elements(framework: FrameworkName) -> bool:
    """Whether a package's references name elements, by its own identity declaration.

    A package that composes an identity from an action and a place grades
    against references that cite elements, so an extraction can lose one by
    dropping an element; a package identified by a catalog requirement cites
    none. Read off the registry's declaration so no name is spelled here.
    """
    return IDENTIFIER_OF[framework] is None


def refuse_unpaired(end_to_end: ScoredRun, analysis: ScoredRun) -> None:
    """The pair has to be one run of each mode over one corpus, or it says nothing."""
    if end_to_end.mode != END_TO_END or analysis.mode != ANALYSIS:
        raise ValueError(
            f"the pair has to be one {END_TO_END!r} run and one {ANALYSIS!r} run,"
            f" in that order; got {end_to_end.mode!r} and {analysis.mode!r}"
        )
    refuse_incomparable([end_to_end, analysis])


def extracted_model(artifact_path: Path | str, case_id: str) -> SystemModel:
    """The model the end-to-end run's lanes read for one case, off its report.

    The report embeds the model it was written against, which for an
    end-to-end run is the extraction. Read through the same validator the
    service applies, so a report whose model would not load is refused by
    name rather than scored as an empty extraction.
    """
    path = reports_dir(artifact_path) / f"{case_id}.report.json"
    if not path.is_file():
        raise ProvenanceError(
            f"{path}: no report for {case_id}; an end-to-end run's extracted"
            " model is read off its report, so the fates cannot say what the"
            " model lacked"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return SystemModel.model_validate(raw["system_model"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ProvenanceError(f"{path}: cannot read its system model: {exc}") from exc


def _fate(reference: str, end_to_end: frozenset[str], analysis: frozenset[str]) -> Fate:
    if reference in end_to_end:
        return "both" if reference in analysis else "recovered"
    return "downstream" if reference not in analysis else "extraction"


def _lacked(
    reference_elements: Sequence[str], extraction: ExtractionScore
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    named = set(reference_elements)
    missing = tuple(sorted(named & set(extraction.missing)))
    differing = tuple(
        f"{check.element_id}.{check.attribute}: {check.blessed} -> {check.extracted}"
        for check in extraction.differing
        if check.element_id in named
    )
    return missing, differing


def attribute_handoff(
    end_to_end: ScoredRun,
    analysis: ScoredRun,
    corpus: Mapping[str, GoldenCase],
    models: Mapping[str, SystemModel],
) -> list[CaseHandoff]:
    """Every shared ``(framework, case)``, each reference given its fate.

    ``models`` maps a case ID to the end-to-end run's extracted model for it,
    which :func:`extracted_model` reads off the report bundle; the caller
    supplies them so this stays a function over values, as ``losses`` is.
    A STRIDE case whose model is absent from the mapping gets its fates and
    no extraction, and says so in its warnings.
    """
    refuse_unpaired(end_to_end, analysis)
    return [
        _case_handoff(scope, end_to_end, analysis, corpus, models)
        for scope in sorted(end_to_end.cases & analysis.cases)
    ]


def _case_handoff(
    scope: Scope,
    end_to_end: ScoredRun,
    analysis: ScoredRun,
    corpus: Mapping[str, GoldenCase],
    models: Mapping[str, SystemModel],
) -> CaseHandoff:
    framework, case_id = scope
    found = end_to_end.matched[scope]
    blessed = analysis.matched[scope]
    count = end_to_end.references[scope]
    if count != analysis.references[scope]:
        raise ValueError(
            f"{case_id}: {count} references in one run and"
            f" {analysis.references[scope]} in the other; two counts of one"
            " case's references share no coordinate system"
        )
    case = corpus.get(case_id)
    warnings: list[str] = []
    if not names_elements(framework) or case is None:
        # A catalog-identified package maps a reference to no element, and a
        # case outside the corpus handed in maps to nothing at all; the fates
        # still read, because they need only the two matched sets.
        if case is None:
            warnings.append(f"{case_id}: not in the corpus handed in; fates only")
        indexed = names_elements(framework)
        references = (
            [str(index) for index in range(count)]
            if indexed
            else sorted(found | blessed)
        )
        named = tuple(
            ReferenceFate(reference, _fate(reference, found, blessed), None)
            for reference in references
        )
        return CaseHandoff(
            framework,
            case_id,
            named,
            warnings=tuple(warnings),
            unnamed_downstream=0 if indexed else count - len(found | blessed),
        )

    claims = case.claims_for(framework)
    if len(claims) != count:
        raise ValueError(
            f"{case_id}: the corpus holds {len(claims)} {framework} references and the"
            f" runs were scored against {count}; the corpus on disk is not the"
            " one the runs were scored against"
        )
    model = models.get(case_id)
    extraction = (
        score_extraction(
            case, ExtractionResult(case_id=case_id, extracted=model, issues=())
        )
        if model is not None
        else None
    )
    if extraction is None:
        warnings.append(f"{case_id}: no extracted model handed in; fates only")
    rows: list[ReferenceFate] = []
    for index, claim in enumerate(claims):
        reference = str(index)
        fate = _fate(reference, found, blessed)
        missing: tuple[str, ...] = ()
        differing: tuple[str, ...] = ()
        if fate == "extraction" and extraction is not None:
            missing, differing = _lacked(claim.affected_element_ids, extraction)
        rows.append(
            ReferenceFate(
                reference,
                fate,
                claim.tier == "must-find",
                missing_elements=missing,
                differing_attributes=differing,
            )
        )
    return CaseHandoff(framework, case_id, tuple(rows), extraction, tuple(warnings))


def pooled(rows: Sequence[CaseHandoff]) -> dict[str, Any]:
    """Fates over the corpus, counted rather than averaged, and what the extraction losses lacked."""
    totals: Counter[str] = Counter()
    must_find: Counter[str] = Counter()
    lacked: Counter[str] = Counter()
    for row in rows:
        for entry in row.fates:
            totals[entry.fate] += 1
            must_find[entry.fate] += bool(entry.must_find)
            if entry.fate == "extraction":
                if entry.missing_elements:
                    lacked["element_missing"] += 1
                elif entry.differing_attributes:
                    lacked["attribute_differs"] += 1
                elif row.extraction is not None:
                    lacked["held_the_place"] += 1
                else:
                    lacked["unread"] += 1
    return {
        "cases": len(rows),
        "references": sum(totals.values()),
        "by_fate": {fate: totals[fate] for fate in FATES},
        "must_find_by_fate": {fate: must_find[fate] for fate in FATES},
        # What the extracted model lacked at each extraction loss, one kind
        # per row in the order decided: a missing element outranks a differing
        # attribute on the elements that were there, and a row with neither
        # lost on a model that held the place.
        "extraction_lacked": {
            kind: lacked[kind]
            for kind in (
                "element_missing",
                "attribute_differs",
                "held_the_place",
                "unread",
            )
        },
        "cases_with_crossings_mismatch": sum(
            1
            for row in rows
            if row.extraction is not None and not row.extraction.crossings_match
        ),
    }


def warnings_for(end_to_end: ScoredRun, analysis: ScoredRun) -> list[str]:
    """Every comparability warning but the one this pair exists to raise."""
    return [
        warning
        for warning in comparability_warnings([end_to_end, analysis])
        if not warning.startswith(_MODE_WARNING_PREFIX)
    ]


def render(rows: Sequence[CaseHandoff], warnings: Sequence[str]) -> None:
    """One line per case, one column per fate, then what the extraction losses lacked."""
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(
        "\nextraction losses (an end-to-end run against the analysis-mode run beside it)"
    )
    print(
        f"  {'framework':10} {'case':26} "
        + " ".join(f"{fate:>11}" for fate in FATES)
        + f" {'crossings':>10}"
    )
    for row in rows:
        counts = row.by_fate
        crossings = (
            "-"
            if row.extraction is None
            else ("match" if row.extraction.crossings_match else "differ")
        )
        print(
            f"  {row.framework:10} {row.case:26} "
            + " ".join(f"{counts[fate]:>11}" for fate in FATES)
            + f" {crossings:>10}"
        )
        for note in row.warnings:
            print(f"    note: {note}")
        for entry in row.fates:
            if entry.fate != "extraction":
                continue
            what = (
                f"missing {', '.join(entry.missing_elements)}"
                if entry.missing_elements
                else (
                    "; ".join(entry.differing_attributes)
                    if entry.differing_attributes
                    else ("held the place" if row.extraction is not None else "unread")
                )
            )
            mark = " must-find" if entry.must_find else ""
            print(f"    extraction: reference {entry.reference}{mark}: {what}")
    totals = pooled(rows)
    print(
        f"pooled over {totals['cases']} case(s): {totals['references']} references, "
        + ", ".join(
            f"{fate} {totals['by_fate'][fate]} (must-find {totals['must_find_by_fate'][fate]})"
            for fate in FATES
        )
        + "; extraction losses lacked: "
        + ", ".join(
            f"{kind} {count}" for kind, count in totals["extraction_lacked"].items()
        )
        + " (instrument, non-gating)"
    )


def artifact(rows: Sequence[CaseHandoff], warnings: Sequence[str]) -> dict[str, Any]:
    return {
        "warnings": list(warnings),
        "cases": [row.to_json() for row in rows],
        "aggregate": pooled(rows),
    }
