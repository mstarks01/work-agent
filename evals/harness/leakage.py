"""Whether a generation input carries a reference answer.

The quality-audit skill's three-condition diagnostic feeds verified material to
generation: the blessed ``model.json`` in condition B, the signed ``facts.json``
in condition C. Both are written by the people who wrote the reference claims,
so an expected answer can reach them by accident — a claim sentence pasted into
a note, a requirement identifier, a tier label. A condition whose input holds
one measures recall of the answer it was handed.

This finds the leaks that are mechanically detectable: a reference claim's
sentence or rationale, verbatim after case and whitespace are folded; any ASVS
requirement identifier; and a tier label. It cannot find a paraphrase, so a
clean result means none of these shapes is present, not that nothing leaked.
The reviewer's own exposure is a separate fact, recorded on the experiment row.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

#: An ASVS requirement identifier, in the shape a reference record carries.
_REQUIREMENT = re.compile(r"\bV\d{1,2}\.\d{1,2}\.\d{1,2}\b")

#: The tier labels an input may not carry. ``expected``, the other tier, is an
#: ordinary word and would flag ordinary prose.
_TIER_LABELS = ("must-find",)

#: Rationale shorter than this is too generic to be an answer: "see above".
_MIN_NOTE_CHARS = 24


def _folded(text: str) -> str:
    return " ".join(text.casefold().split())


def answer_leaks(
    records: Iterable[Mapping[str, Any]], inputs: Mapping[str, str]
) -> list[str]:
    """Every reference answer an input text carries, named by input and kind.

    ``records`` are the raw reference claims of every framework the case
    declares; ``inputs`` maps an input's name to its text.
    """
    answers = []
    for record in records:
        answers.append(("claim", _folded(str(record.get("claim", "")))))
        notes = _folded(str(record.get("notes", "")))
        if len(notes) >= _MIN_NOTE_CHARS:
            answers.append(("rationale", notes))
    leaks = []
    for name, text in inputs.items():
        folded = _folded(text)
        leaks += [
            f"{name}: carries a reference {kind}: {answer[:60]!r}"
            for kind, answer in answers
            if answer and answer in folded
        ]
        leaks += [
            f"{name}: carries the requirement identifier {found}"
            for found in sorted(set(_REQUIREMENT.findall(text)))
        ]
        leaks += [
            f"{name}: carries the tier label {tier!r}"
            for tier in _TIER_LABELS
            if tier in folded
        ]
    return leaks
