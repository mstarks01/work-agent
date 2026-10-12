"""Time the early question set of a paused job on one corpus model (E6).

Run from the repository root, with ``ANALYSIS_OFFLINE=1``::

    python evals/experiments/QA-2026-10-12-02/build_time.py CASE SELECTION

``SELECTION`` is the job's frameworks as JSON. E6 passed the selection E3
timed: STRIDE with no options and ASVS at level 3. The script reads the case's
blessed ``model.json`` with that selection, and reads ``AnswerState.questions`` 20 times in each state: with no
answers, and with the first thirty "yes" answers of an all-yes walk where the
list asks for thirty; each with the memos of ``early_questions`` and
``answer_effects`` cleared before each read ("nothing kept") or left full
("kept"). The ``key_ref`` cache stays warm in both. It refuses a holdout case.
At a revision with no memo, the two rows of a state read the same code.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

from analysis_service import answer_effects, early_questions
from analysis_service.fact_answers import FactAnswer
from analysis_service.system_model import SystemModel
from evals.harness.early_answerers import ANSWERERS, walk
from evals.harness.early_policies import Selection
from evals.harness.early_rounds import paused_state
from evals.harness.reference import refuse_holdout

#: Reads per row, and "yes" answers in the answered state.
READS = 20
ANSWERED = 30


def _clear() -> None:
    for module, name in ((early_questions, "_lists"), (answer_effects, "_effects")):
        memo = getattr(module, name, None)
        if memo is not None:
            memo.clear()


def _times(
    model: SystemModel, frameworks: Selection, answers: list[FactAnswer], *, kept: bool
) -> list[float]:
    found = []
    _ = paused_state(model, frameworks, answers, ()).questions
    for _ in range(READS):
        if not kept:
            _clear()
        start = time.perf_counter()
        _ = paused_state(model, frameworks, answers, ()).questions
        found.append((time.perf_counter() - start) * 1000)
    return found


def main(case: str, frameworks: Selection) -> None:
    refuse_holdout(case)
    path = Path("evals/corpus") / case / "model.json"
    model = SystemModel.model_validate_json(path.read_text(encoding="utf-8"))
    walked = walk(model, frameworks, ANSWERERS["yes"], effects=True, budget=200)
    yes = [
        step.answer
        for step in walked.steps
        if step.answer is not None and step.answer.value == "yes"
    ][:ANSWERED]
    # A model whose list holds fewer than thirty capability questions to
    # answer "yes" is timed with no answers only.
    states = [[], yes] if len(yes) == ANSWERED else [[]]
    rows: dict[str, object] = {
        "early": len(paused_state(model, frameworks, [], ()).questions.early)
    }
    for answers in states:
        for kept in (False, True):
            times = _times(model, frameworks, answers, kept=kept)
            rows[f"{len(answers)} answers, {'kept' if kept else 'nothing kept'}"] = {
                "p50_ms": round(statistics.median(times), 1),
                "p95_ms": round(statistics.quantiles(times, n=20)[-1], 1),
                "max_ms": round(max(times), 1),
            }
    print(json.dumps({"case": case, "elements": len(list(model.elements())), **rows}))


if __name__ == "__main__":
    main(sys.argv[1], json.loads(sys.argv[2]))
