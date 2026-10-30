"""An experiment row keeps each amount that its author wrote.

A row written through a shell with double quotes loses a dollar amount: the
shell reads ``$0`` as its own name, such as ``/bin/bash``, and ``$2`` as an
empty argument. The row then says ``/bin/bash.2281`` or ``about .28``, and the
JSON still loads, so nothing else fails.
"""

from __future__ import annotations

import re

import pytest

from tests.source_tree import REPO_ROOT

pytestmark = pytest.mark.lint

#: What a lost ``$0`` or ``$2`` leaves: a shell path, or a decimal point with
#: no digit, letter or sign before it.
_LOST_AMOUNT = re.compile(r"/bin/\w*sh\.\d|(?<![\w$.\d])\.\d+\b")


def test_no_experiment_row_lost_an_amount_to_the_shell():
    found = [
        f"{path.name}:{number}: {match.group()!r}"
        for path in sorted((REPO_ROOT / "evals" / "experiments").glob("*.jsonl"))
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1)
        for match in _LOST_AMOUNT.finditer(line)
    ]

    assert not found, found
