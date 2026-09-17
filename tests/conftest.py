"""Session-wide fixtures.

The one thing here exists because the shipped ``config/model_tiers.toml``
selects no vendor: see :data:`tests.factories.TEST_TIER_ENV`.
"""

from __future__ import annotations

import pytest

from tests.factories import TEST_CREDENTIAL_ENV, TEST_TIER_ENV


@pytest.fixture(autouse=True)
def _selected_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give ``Deployment.from_env()`` a vendor selection to find.

    Autouse and process-wide because the eval harness resolves its deployment
    from the ambient environment rather than taking one — so without this, every
    harness-driving test would stop on the onboarding error instead of the
    behaviour it means to assert.

    A test that passes ``env=`` explicitly is untouched: the loaders read the
    mapping they are given, never both. That is what keeps the tests asserting
    the *unselected* case honest — they pass ``env={}`` and still see it.
    """
    for var, value in (TEST_TIER_ENV | TEST_CREDENTIAL_ENV).items():
        monkeypatch.setenv(var, value)


@pytest.fixture(scope="session")
def corpus_case():
    """The first corpus case, loaded once, for the tests that drive a real one.

    In the conftest rather than in either file that wants it: two modules each
    loading the corpus is two spellings of one fixture, and the duplicate-body
    lint is right to refuse them. Session-scoped because loading the corpus
    reads every case's files and nothing here mutates what it hands back.
    """
    from evals.harness.artifact import REPO_ROOT
    from evals.harness.reference import load_corpus

    return load_corpus(REPO_ROOT / "evals" / "corpus")[0]
