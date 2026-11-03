"""Count the store reads of E7 of QA-2026-11-02-05 by attacker, for E4.

Usage, from the repository root: ``displacement.py``. E7 ran the
information-disclosure lane on cases 04, 05 and 13, two passes each, with the
#2134 contrast exemplar (treatment, ``item4-0*`` and ``item4-13-*``) and
without it (control, ``item4-ctl-*``), on one tree. Those drafts state no
attacker position, so each ``read`` of a store is given a class by its text:

* ``compromised-reader``: the title or description says the attacker
  compromises the reader or runs code in it;
* ``grant-holder``: otherwise, the draft cites the authentication fact of a
  flow into the store, so its attacker reads through access to the store;
* ``other``: every other read of a store, such as a storage copy.

A word rule raises a lead and rules out nothing, so ``runs/displacement.json``
keeps each draft's title, its class and the words or evidence ref that decided
the class, for a person to check. Writes that
file and prints the counts.
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
E7_RUNS = HERE.parent / "QA-2026-11-02-05" / "runs"
ARMS = {
    "treatment": ("item4-04-p*.json", "item4-05-p*.json", "item4-13-p*.json"),
    "control": ("item4-ctl-*.json",),
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


def reads(arm: str) -> list[dict[str, str]]:
    """Each read of a store in one arm's runs, with its class."""
    return [
        {"run": path.stem, "title": claim["title"], "class": kind, "because": why}
        for pattern in ARMS[arm]
        for path in sorted(E7_RUNS.glob(pattern))
        for claim in json.loads(path.read_text(encoding="utf-8"))["claims"]
        if claim["verb"] == "read"
        and any(ref.startswith("store:") for ref in claim["affected_element_ids"])
        for kind, why in [_class(claim)]
    ]


if __name__ == "__main__":
    result = {arm: reads(arm) for arm in ARMS}
    counts = {
        arm: dict(Counter(row["class"] for row in rows)) for arm, rows in result.items()
    }
    (HERE / "runs" / "displacement.json").write_text(
        json.dumps({"counts": counts, "reads": result}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(counts, indent=2))
