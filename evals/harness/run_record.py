"""What an early-question instrument's output file was read from (#2029).

A reading of the early list is reproducible only with the commit and tree it
ran on, the instrument's own table of policies, answerers and budgets, and
each input file with the framework selections read on it. The record holds
each of those in a field, so a reader compares two output files by field and
never by a ledger sentence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from analysis_service.claims import FrameworkName
from evals.harness.provenance import checkout_state

#: The selections read on one input file, by name, each a framework's options.
Selections = Mapping[str, Mapping[FrameworkName, Mapping[str, Any]]]


def run_record(
    command: str, table: Mapping[str, Any], inputs: Mapping[Path, Selections]
) -> dict[str, Any]:
    """The record of one run of ``command``.

    ``table`` is the instrument's own policies, answerers and budgets.
    ``inputs`` maps each file read to the selections read on it; a file that
    holds no job, such as a blessed model, maps to none. ``clean`` is false
    where the tree differs from ``commit``, so the record never names a commit
    the run did not read. Raises
    :class:`~evals.harness.provenance.ProvenanceError` where there is no
    checkout to ask.
    """
    commit, clean = checkout_state()
    return {
        "command": command,
        "commit": commit,
        "clean": clean,
        "table": dict(table),
        "inputs": [
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "selections": selections,
            }
            for path, selections in sorted(inputs.items())
        ],
    }
