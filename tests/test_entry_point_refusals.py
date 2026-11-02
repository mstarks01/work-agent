"""Each command-line entry point refuses a configuration it cannot run on in one line.

The harness loads the resilience file to bound each corpus source, and a
replay loads the model tiers. A :class:`~analysis_service.errors.ConfigError`
from either names the file or the variable to fix, and a traceback over it
hides that line. ``tests/test_evals_run.py`` holds the same refusal for
``run.main``.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from analysis_service.deployment import RESILIENCE_VAR
from evals import build_diagrams
from evals.critic_review.__main__ import main as critic_review_main
from tests.factories import TEST_TIER_ENV
from tests.test_evals_assembly import OLD_BASELINE
from webapp import offline_sitting, review, sitting


def _offline_sitting(tmp_path) -> int:
    return offline_sitting.main(
        ["--submitted-by", "sam", "--out", str(tmp_path / "sitting.html")]
    )


#: Each entry point that loads the corpus, driven with the arguments it needs.
CORPUS_READERS: dict[str, Callable[..., int]] = {
    "build_diagrams": lambda tmp_path: build_diagrams.main(),
    "sitting": lambda tmp_path: sitting.main(["--submitted-by", "sam"]),
    "offline_sitting": _offline_sitting,
    "review": lambda tmp_path: review.main(
        ["--voter", "sam", "--artifact", str(OLD_BASELINE)]
    ),
}


@pytest.mark.parametrize("name", sorted(CORPUS_READERS))
def test_a_missing_resilience_file_ends_in_one_line(
    name, tmp_path, monkeypatch, capsys
):
    missing = tmp_path / "missing.toml"
    monkeypatch.setenv(RESILIENCE_VAR, str(missing))
    monkeypatch.setattr(
        "webapp.offline_sitting.submit_spine.gh_login", lambda root: "sam"
    )

    assert CORPUS_READERS[name](tmp_path) == 1
    printed = capsys.readouterr().err
    assert printed.startswith("cannot run on this configuration: ")
    assert str(missing) in printed
    assert printed.count("\n") == 1


def test_the_critic_review_refuses_tiers_that_select_no_vendor(monkeypatch, capsys):
    for var in TEST_TIER_ENV:
        monkeypatch.delenv(var)

    assert critic_review_main(["--accept-cost", "0"]) == 1
    printed = capsys.readouterr().err
    assert printed.splitlines()[-1].startswith("cannot run on this configuration: ")
    assert "no vendor selected" in printed
    assert "Traceback" not in printed
