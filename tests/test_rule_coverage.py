"""Every package's rules, against the corpus that has to exercise them.

A **Candidate** rule that fires on no corpus case is reading a shape the corpus
does not contain, or a shape the extraction never produces. The two need
different fixes — a case for the first, a rule edit for the second — and this
module does not tell them apart. What it does is make the question visible at
all: every other check in the repo passes a dead rule silently.

``evals/harness/triggers.py`` scores per *reference threat* and so never names a
rule; ``test_knowledge_lints`` checks a rule has reference material, which a
dead rule has too. So this is the one place a rule's own firing is asserted.

Deterministic over the blessed ``model.json`` and free of provider calls, which
is why it gates on every PR rather than waiting for a sweep.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stride_service.frameworks import PACKAGES
from stride_service.system_model import SystemModel
from stride_service.validation import parse_and_validate

CORPUS_DIR = Path(__file__).resolve().parents[1] / "evals" / "corpus"

#: Rules the corpus does not exercise, each with the reason it is acceptable.
#: Every entry here is the *first* reading — the rule is right and no case
#: describes the shape — so every one is a gap in the corpus rather than in the
#: rule. A rule whose predicate cannot match what extraction produces does not
#: belong here; it belongs fixed.
UNEXERCISED: dict[str, str] = {}


@pytest.fixture(scope="module")
def models() -> list[SystemModel]:
    """Every blessed model, through the shipped validity gate.

    Rules only ever see valid models in production, so a case whose blessed
    model would be rejected there cannot say anything about a rule's firing.
    """
    loaded = []
    for case_dir in sorted(path for path in CORPUS_DIR.iterdir() if path.is_dir()):
        model, issues = parse_and_validate(
            json.loads((case_dir / "model.json").read_text())
        )
        assert model is not None and not issues, f"{case_dir.name}: {issues}"
        loaded.append(model)
    return loaded


def dead_rules(models: list[SystemModel]) -> set[str]:
    """Every registered rule that fires on none of the models."""
    return {
        rule.rule_id
        for package in PACKAGES.values()
        for rule in package.rules
        if not any(rule.fire(model) for model in models)
    }


def test_no_rule_fires_nowhere_without_a_stated_reason(models):
    undeclared = sorted(dead_rules(models) - set(UNEXERCISED))
    assert not undeclared, (
        "these rules fire on no corpus case, and nothing says why:"
        f" {undeclared}. Either the corpus lacks the shape — add the rule to"
        " UNEXERCISED with the reason — or the rule reads a shape extraction"
        " does not produce, which is a defect in the rule."
    )


def test_the_exemption_list_does_not_rot(models):
    """A rule that starts firing has to leave the list, or it excuses nothing."""
    revived = sorted(set(UNEXERCISED) - dead_rules(models))
    assert not revived, (
        f"these rules now fire and are still exempted: {revived}. Remove them"
        " from UNEXERCISED."
    )


def test_every_exempted_rule_is_a_rule_some_package_declares(models):
    del models
    known = {rule.rule_id for package in PACKAGES.values() for rule in package.rules}
    assert set(UNEXERCISED) <= known, (
        f"UNEXERCISED names rules no package declares: "
        f"{sorted(set(UNEXERCISED) - known)}"
    )
