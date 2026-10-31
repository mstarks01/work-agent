"""The appended answers source of each arm of QA-2026-10-31-02-E1, for one case:
every question kind the STRIDE prior names for each element's type, answered
yes or no. Run it as ``python build_answers.py <case> <out dir>``."""

import sys
from pathlib import Path

from analysis_service.claims import UnknownRef
from analysis_service.early_questions import QUESTION_PRIOR, element_type
from analysis_service.fact_answers import FactAnswer
from analysis_service.links import with_link_answers
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.sources import Source, render_sources
from analysis_service.system_model import SystemModel


def main(case: str, out: Path) -> None:
    """Write ``<case>-yes.txt`` and ``<case>-no.txt`` into ``out``."""
    model = SystemModel.model_validate_json(
        Path(f"evals/corpus/{case}/model.json").read_text()
    )
    rates = QUESTION_PRIOR["stride"].rates
    for value in ("yes", "no"):
        facts = []
        for element in model.elements():
            for kind in rates.get(element_type(element), {}):
                if kind not in QUESTION_KINDS:
                    continue
                key = UnknownRef(element_id=element.id, question=kind).key
                spec = QUESTION_KINDS[kind]
                if spec.facets:
                    facts.append(
                        FactAnswer.model_validate(
                            {"key": key, "facets": {f.id: value for f in spec.facets}}
                        )
                    )
                else:
                    facts.append(FactAnswer(key=key, value=value))
        (answers,) = [
            s
            for s in with_link_answers([Source.description("x")], [], facts)
            if s.kind == "answers"
        ]
        (out / f"{case}-{value}.txt").write_text(
            render_sources([answers]), encoding="utf-8"
        )
        print(value, len(facts), "answers,", len(answers.text), "chars")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
