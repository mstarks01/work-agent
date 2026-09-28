"""A case's answers file, and the withheld-sentence test it can describe (#1225).

``evals/answers/<case>.json`` holds a person's answers to a case's open facts,
and who signed them. No corpus case has an owner who knows more than its
source (``QA-2026-09-26-03-E14``), so the file may also withhold facts the
source does state: it names the phrases to take out of the sources and the
model fields to set back, and its answers give those same facts back. Three
arms then compare one case:

* ``analysis``: the full case, the ceiling;
* ``withheld``: the case with the facts taken out, the floor;
* ``answered``: the withheld case with the answers written in.

Every answer is then the source's own statement, and nobody invents one.

**One reader of the file.** :func:`load_answer_file` parses and checks it,
and :func:`withheld_case` applies it, for both modes. A withheld phrase must
occur exactly once across the sources, and afterwards no withheld phrase may
remain in a source or in a model field, and every element's excerpt must still
occur in a source. Each check fails closed, because a fact left behind in an
excerpt reaches the lanes and makes the floor a ceiling.

**A paraphrase is the reviewer's to find.** The phrase check finds a withheld
phrase and never a restatement of it: case 01's draft passed with "an
authenticated session" still in a flow's description. So the file lists
every field it sets back, a person reads the withheld model before signing,
and ``targets`` names the reference claims each withheld fact supports.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from analysis_service.questions import FactAnswer, check_fact_answers
from analysis_service.sources import Source
from analysis_service.system_model import SystemModel
from evals.harness.modes import EvalRunError
from evals.harness.reference import GoldenCase

__all__ = ["AnswerFile", "load_answer_file", "withheld_case"]


@dataclass(frozen=True)
class AnswerFile:
    """One case's signed answers, and what the case withholds for them."""

    #: Who signed the file. A file nobody signed is refused before it runs.
    signed_by: str
    #: Who drafted it: an agent may draft, never sign.
    drafted_by: str
    answers: tuple[FactAnswer, ...]
    #: Each withheld phrase, and the text that takes its place.
    withheld_text: tuple[tuple[str, str], ...]
    #: Each model field set back: the element, the field, the value it gets.
    withheld_fields: tuple[tuple[str, str, str], ...]


def _pattern(text: str) -> re.Pattern[str]:
    """``text`` matched with any run of whitespace standing for any other."""
    return re.compile(r"\s+".join(re.escape(word) for word in text.split()))


def load_answer_file(path: Path) -> AnswerFile | None:
    """The file at ``path``, or ``None`` where it does not exist or nobody signed it."""
    if not path.is_file():
        return None
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if not raw.get("signed_by"):
        return None
    withheld = raw.get("withheld", {})
    return AnswerFile(
        signed_by=raw["signed_by"],
        drafted_by=raw.get("drafted_by", ""),
        answers=tuple(FactAnswer.model_validate(entry) for entry in raw["answers"]),
        withheld_text=tuple(
            (entry["text"], entry["replacement"])
            for entry in withheld.get("source", [])
        ),
        withheld_fields=tuple(
            (entry["element_id"], entry["field"], entry["value"])
            for entry in withheld.get("model", [])
        ),
    )


def _withheld_sources(
    sources: tuple[Source, ...], withheld: tuple[tuple[str, str], ...]
) -> tuple[Source, ...]:
    texts = [source.text for source in sources]
    for phrase, replacement in withheld:
        pattern = _pattern(phrase)
        hits = sum(len(pattern.findall(text)) for text in texts)
        if hits != 1:
            raise EvalRunError(
                f"a withheld phrase occurs {hits} times across the sources, and"
                f" it must occur once: {phrase[:60]!r}"
            )
        texts = [pattern.sub(replacement, text) for text in texts]
    return tuple(
        Source.model_validate({**source.model_dump(), "text": text})
        for source, text in zip(sources, texts, strict=True)
    )


def _withheld_model(
    model: SystemModel, withheld: tuple[tuple[str, str, str], ...]
) -> SystemModel:
    raw = model.model_dump(mode="json")
    elements = {
        element["id"]: element
        for group in raw.values()
        if isinstance(group, list)
        for element in group
        if isinstance(element, dict) and "id" in element
    }
    for element_id, field, value in withheld:
        element = elements.get(element_id)
        if element is None or field not in element:
            raise EvalRunError(f"the model holds no field {field!r} on {element_id!r}")
        element[field] = value
    return SystemModel.model_validate(raw)


def withheld_case(case: GoldenCase, answer_file: AnswerFile) -> GoldenCase:
    """The case with the file's facts taken out of its sources and its model."""
    sources = _withheld_sources(case.sources, answer_file.withheld_text)
    model = _withheld_model(case.model, answer_file.withheld_fields)
    texts = " ".join(source.text for source in sources)
    fields = json.dumps(model.model_dump(mode="json"), ensure_ascii=False)
    for phrase, _ in answer_file.withheld_text:
        if _pattern(phrase).search(texts) or _pattern(phrase).search(fields):
            raise EvalRunError(
                f"a withheld phrase is still in the case: {phrase[:60]!r}"
            )
    for element in model.elements():
        if element.source_excerpt and not _pattern(element.source_excerpt).search(
            texts
        ):
            raise EvalRunError(
                f"{element.id}'s excerpt is no longer in the sources; set it to"
                " a sentence that is"
            )
    checked = replace(case, sources=sources, model=model)
    try:
        check_fact_answers(answer_file.answers, checked.model, None)
    except ValueError as error:
        raise EvalRunError(f"{case.id}: {error}") from error
    return checked
