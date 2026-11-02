"""Issue and read the blind ballot of E7, the fresh control arm of E3.

The ballot mixes the case 05 target drafts of both arms, the treatment runs
``item4-05-p*`` and the control runs ``item4-ctl-05-p*``, under one set of
group letters, so a row does not show its arm. It also holds the control
arm's copy-control and no-read-control drafts of cases 13 and 04. Each check
asks the question and takes the verdicts of its spec in ``build_ballot.CHECKS``.

Usage, from the repository root:

* ``control_ballot.py issue`` writes ``control-ballot-issued.csv`` and
  ``control-ballot-key.json``.
* ``control_ballot.py read`` reads ``control-ballot-returned.csv`` with
  ``read_ballot.read``, prints the counts and writes them to
  ``runs/control-ballot-read.json``.
"""

import json
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_ballot import CHECKS, HERE, RUNS, Check, CheckSpec, write
from read_ballot import read

CONTROL_CHECKS: Mapping[Check, CheckSpec] = {
    "item4": replace(CHECKS["item4"], pattern="item4-*05-p*.json"),
    "item4-copy-control": replace(
        CHECKS["item4-copy-control"], pattern="item4-ctl-13-p*.json"
    ),
    "item4-no-read-control": replace(
        CHECKS["item4-no-read-control"], pattern="item4-ctl-04-p*.json"
    ),
}
ISSUED = HERE / "control-ballot-issued.csv"
KEY = HERE / "control-ballot-key.json"

if __name__ == "__main__":
    if sys.argv[1:] == ["issue"]:
        write(CONTROL_CHECKS, ISSUED, KEY)
    elif sys.argv[1:] == ["read"]:
        counts = json.dumps(
            read(CONTROL_CHECKS, HERE / "control-ballot-returned.csv", ISSUED, KEY),
            indent=1,
        )
        (RUNS / "control-ballot-read.json").write_text(counts + "\n")
        print(counts)
    else:
        raise SystemExit("usage: control_ballot.py issue | read")
