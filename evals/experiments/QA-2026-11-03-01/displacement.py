"""Count the store reads of two arms by attacker, for E4 and E5.

Usage, from the repository root: ``displacement.py [study]``, where each study
of :data:`STUDIES` names a run folder and the files of each arm:

* ``displacement`` (the default, E4): E7 of QA-2026-11-02-05 ran the
  information-disclosure lane on cases 04, 05 and 13, two passes each, with
  the #2134 contrast exemplar (``item4-0*``, ``item4-13-*``) and without it
  (``item4-ctl-*``), on one tree.
* ``displacement-e5`` (E5): stage 1 of this audit (``id-*``) ran with that
  exemplar and stage 4 (``no4-*``) without it, on 13 cases.

Each ``read`` of a store is given a class by its text:

* ``compromised-reader``: the title or description says the attacker
  compromises the reader or runs code in it;
* ``grant-holder``: otherwise, the draft cites the authentication fact of a
  flow into the store, so its attacker reads through access to the store;
* ``other``: every other read of a store, such as a storage copy.

A word rule raises a lead and rules out nothing, so ``runs/<study>.json``
keeps each draft's title, its class and the words or evidence ref that decided
the class, for a person to check. Writes that file and prints the counts.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
#: Each study: the folder its runs sit in, and the file patterns of each arm.
STUDIES: dict[str, tuple[Path, dict[str, tuple[str, ...]]]] = {
    "displacement": (
        HERE.parent / "QA-2026-11-02-05" / "runs",
        {
            "treatment": (
                "item4-04-p*.json",
                "item4-05-p*.json",
                "item4-13-p*.json",
            ),
            "control": ("item4-ctl-*.json",),
        },
    ),
    "displacement-e5": (
        HERE / "runs",
        {
            "with-exemplar": ("id-[0-9]*.json",),
            "without-exemplar": ("no4-[0-9]*.json",),
        },
    ),
}
COMPROMISE = re.compile(r"\w*compromis\w*|code running|runs? code", re.IGNORECASE)
STORE_AUTH = re.compile(r"(?:unknown|absent):flow:\S*>store:\S*:authentication")


def _class(claim: dict[str, Any]) -> tuple[str, str]:
    """The draft's class, and the words or the evidence ref that decided it."""
    compromise = COMPROMISE.search(f"{claim['title']} {claim['description']}")
    if compromise:
        return "compromised-reader", compromise.group(0)
    auth = STORE_AUTH.search(" ".join(claim.get("evidence_refs") or []))
    if auth:
        return "grant-holder", auth.group(0)
    return "other", ""


def reads(folder: Path, patterns: tuple[str, ...]) -> list[dict[str, str]]:
    """Each read of a store in one arm's runs, with its class."""
    return [
        {"run": path.stem, "title": claim["title"], "class": kind, "because": why}
        for pattern in patterns
        for path in sorted(folder.glob(pattern))
        for claim in json.loads(path.read_text(encoding="utf-8"))["claims"]
        if claim["verb"] == "read"
        and any(ref.startswith("store:") for ref in claim["affected_element_ids"])
        for kind, why in [_class(claim)]
    ]


if __name__ == "__main__":
    study = sys.argv[1] if len(sys.argv) > 1 else "displacement"
    folder, arms = STUDIES[study]
    result = {arm: reads(folder, patterns) for arm, patterns in arms.items()}
    counts = {
        arm: dict(Counter(row["class"] for row in rows)) for arm, rows in result.items()
    }
    (HERE / "runs" / f"{study}.json").write_text(
        json.dumps({"counts": counts, "reads": result}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(counts, indent=2))
