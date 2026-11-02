"""Build the blind ballot of QA-2026-11-02-05 from its run outputs.

The rows are each case 05 draft at a target config store and each control draft
from the E3 runs, and each spoofing draft from the E2 runs, in one shuffled
order with a fixed seed. A row does not name its run: its ``group`` is a letter
given to the run in a second shuffled order, and ``ballot-key.json`` maps each
row to its run.

Each check has a closed set of verdicts, in ``VERDICTS``. For E2, a draft that
repeats another row of the same group in attacker, failed control and fix at
one place takes the verdict ``duplicate`` and names that row in
``duplicate_of``; a ``right`` draft whose flow also holds a ``right`` row of the
same group that impersonates the other end names that row in ``pair_of``. Two
groups are two runs, and a repeat across them is expected.

Usage, from the repository root: ``build_ballot.py``. ``read_ballot.py`` reads
the returned file.
"""

import csv
import json
import random
import string
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

HERE = Path(__file__).parent
RUNS = HERE / "runs"
SEED = 20261102

#: The checks the ballot holds: E2 (item 2), and E3 (item 4) with its controls.
Check = Literal["item2", "item4", "item4-copy-control", "item4-no-read-control"]

#: A verdict on any check. Each check takes the subset ``VERDICTS`` names.
Verdict = Literal[
    "right", "wrong", "duplicate", "same", "different", "yes", "no", "unsure"
]

#: Each check's verdicts. A verdict outside its set is not a ruling.
VERDICTS: Mapping[Check, tuple[Verdict, ...]] = {
    "item2": ("right", "wrong", "duplicate", "unsure"),
    "item4": ("same", "different", "unsure"),
    "item4-copy-control": ("yes", "no", "unsure"),
    "item4-no-read-control": ("yes", "no", "unsure"),
}

#: Each check's runs and, where it rules only some drafts, the stores they
#: must touch: the targets of references 6 and 7, the database of reference
#: 13/11, and the store no component reads. ``None`` rules every draft.
DRAFTS: Mapping[Check, tuple[str, frozenset[str] | None]] = {
    "item4": (
        "item4-05-p*.json",
        frozenset({"store:worker-config", "store:web-application-config"}),
    ),
    "item4-copy-control": ("item4-13-p*.json", frozenset({"store:dispatch-database"})),
    "item4-no-read-control": ("item4-04-p*.json", frozenset({"store:inference-log"})),
    "item2": ("item2-*-p*.json", None),
}

_NO_READ = DRAFTS["item4-no-read-control"][1]
assert _NO_READ is not None
(_NO_READ_STORE,) = _NO_READ

QUESTIONS: Mapping[Check, str] = {
    "item4": (
        "Does the draft name an attacker who runs code in the process that reads"
        " this config store, and reads the credential through that process's own"
        " access, with a remedy on that access or on the credential's lifetime?"
        " same: attacker, credential, path and remedy all agree."
    ),
    "item4-copy-control": (
        "Does the draft state that an attacker who obtains a copy of the dispatch"
        " database reads the crew data in it?"
    ),
    "item4-no-read-control": (
        f"Does the draft name an attacker who reads {_NO_READ_STORE} through a"
        " component's own read access? The sources give no component read access"
        " to it, so yes is a failed control."
    ),
    "item2": (
        "Is this spoofing threat right for the source? duplicate: it repeats"
        " another row of the same group in attacker, failed control and fix at"
        " the same place; name that row in duplicate_of. If it is right and"
        " another right row of the same group at the same flow impersonates the"
        " other end, name that row in pair_of."
    ),
}

COLUMNS = (
    "n",
    "check",
    "group",
    "case",
    "title",
    "description",
    "elements",
    "mitigations",
    "question",
    "verdicts",
    "verdict",
    "duplicate_of",
    "pair_of",
    "note",
)


def _runs(pattern: str) -> list[tuple[str, dict]]:
    """Each run's name and output, in name order."""
    return [
        (path.stem, json.loads(path.read_text())) for path in sorted(RUNS.glob(pattern))
    ]


def drafts() -> list[tuple[Check, str, str, dict]]:
    """Each ruled draft with its check, its run and its case, shuffled."""
    rows: list[tuple[Check, str, str, dict]] = []
    for check, (pattern, stores) in DRAFTS.items():
        for run, output in _runs(pattern):
            case = output["record"]["table"]["case"]
            for claim in output["claims"]:
                if stores is None or stores & set(claim["affected_element_ids"]):
                    rows.append((check, run, case, claim))
    random.Random(SEED).shuffle(rows)
    return rows


def groups(runs: list[str]) -> dict[str, str]:
    """A letter for each run, in an order no reader can tie to the run."""
    order = sorted(set(runs))
    if len(order) > len(string.ascii_uppercase):
        raise ValueError(f"{len(order)} runs, and only 26 group letters")
    random.Random(SEED + 1).shuffle(order)
    return dict(zip(order, string.ascii_uppercase[: len(order)], strict=True))


def main() -> None:
    rows = drafts()
    letter = groups([run for _, run, _, _ in rows])
    key = []
    with (HERE / "ballot-issued.csv").open("w", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(COLUMNS)
        for n, (check, run, case, claim) in enumerate(rows, 1):
            writer.writerow(
                [
                    n,
                    check,
                    letter[run],
                    case,
                    claim["title"],
                    claim["description"],
                    "; ".join(claim["affected_element_ids"]),
                    " | ".join(
                        f"{m['summary']}: {m['detail']}" for m in claim["mitigations"]
                    ),
                    QUESTIONS[check],
                    "/".join(VERDICTS[check]),
                    "",
                    "",
                    "",
                    "",
                ]
            )
            key.append(
                {
                    "n": n,
                    "run": run,
                    "group": letter[run],
                    "sequence": claim["sequence"],
                }
            )
    (HERE / "ballot-key.json").write_text(json.dumps(key, indent=1) + "\n")


if __name__ == "__main__":
    main()
