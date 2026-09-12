"""Which files each prose figure reads, observed rather than named.

Run as a script. It prints one JSON object on stdout: every figure in
``tests.test_doc_figure_lints.FIGURES``, with the paths it read under
``evals/review/submissions/``, plus a control that must report some.

## Why this is a script and not a test

``sys.addaudithook`` cannot be removed once added, so a hook installed in the
test process would stay live for the rest of the suite and audit every
unrelated read. The observation happens here, in one subprocess, and
``tests/test_doc_figure_lints.py`` reads the answer.

## Why a read of one directory settles the question

A merged **Case Sitting** changes exactly one thing in the tree: it adds a file
under ``evals/review/submissions/``. So a figure that never reads that
directory cannot move when a sitting merges. That is an implication rather than
a heuristic, which is what makes this airtight where a check over names is not:
an alias import, a helper three deep, and a direct ``glob`` of the directory
are all the same event here.

**The one thing it does not cover.** A file generated *from* the submissions and
committed would move a figure that reads it when the regeneration landed rather
than when the sitting merged. Nothing is generated that way.

## The control is the point

An audit hook that observes nothing reports every figure clean, and silence
would look exactly like success. So the control runs
:func:`~evals.review_submission.unreviewed_cases`, which reads the submissions
by construction, and the reader of this output refuses an empty control.

The control proves the hook observes something, not that it observes
everything: the events below overlap, so dropping ``open`` alone still leaves
``os.scandir`` reporting the directory. That redundancy is the point. An empty
control means the hook is dead.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: What a sitting adds, and so the only read that can make a figure move.
SUBMISSIONS = "evals/review/submissions"

#: Audit events that carry a path this cares about. Named rather than "every
#: event", because the hook runs on each one and a figure that loads the corpus
#: raises tens of thousands: scanning every argument of every event costs more
#: than the whole subprocess. Each name below is an event whose first argument
#: is the path being reached — reading a file, and listing or matching a
#: directory, which is how a directory of submissions is found in the first
#: place.
EVENTS = frozenset(
    {
        "open",
        "os.listdir",
        "os.scandir",
        "pathlib.Path.glob",
        "pathlib.Path.rglob",
        "pathlib.Path.iterdir",
    }
)


def main(argv: Sequence[str] | None = None) -> int:
    touched: set[str] = set()

    def hook(event: str, args: tuple[object, ...]) -> None:
        if event in EVENTS and args:
            touched.add(str(args[0]))

    sys.addaudithook(hook)

    sys.path.insert(0, str(REPO_ROOT))
    from evals.review_submission import unreviewed_cases
    from tests.test_doc_figure_lints import FIGURES

    def under_submissions() -> list[str]:
        return sorted(path for path in touched if SUBMISSIONS in path)

    figures = {}
    for figure in FIGURES:
        touched.clear()
        figure.compute()
        figures[figure.name] = under_submissions()

    touched.clear()
    unreviewed_cases(REPO_ROOT)
    control = under_submissions()

    json.dump({"figures": figures, "control": control}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
