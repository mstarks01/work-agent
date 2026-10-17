"""Session-wide fixtures.

The one thing here exists because the shipped ``config/model_tiers.toml``
selects no vendor: see :data:`tests.factories.TEST_TIER_ENV`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from analysis_service.config_files import SETTINGS_DIR_VAR
from analysis_service.offline import OFFLINE_ENV
from analysis_service.vendors import VENDORS, refused_var

# The shared assertion helpers in ``tests.factories`` show their values in a
# failure only where pytest rewrites the module before its first import.
pytest.register_assert_rewrite("tests.factories")

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
    # A key the developer's shell exports would make every build refuse it.
    for vendor in VENDORS.values():
        for var in vendor.all_env_vars(secret_only=True):
            monkeypatch.delenv(refused_var(var), raising=False)


@pytest.fixture(scope="session")
def _empty_settings_dir(tmp_path_factory: pytest.TempPathFactory) -> str:
    return str(tmp_path_factory.mktemp("settings"))


@pytest.fixture(autouse=True)
def _settings_dir(monkeypatch: pytest.MonkeyPatch, _empty_settings_dir: str) -> None:
    """Point the first-run app at an empty settings directory, so no test
    reads the ``settings.env`` of the developer's machine."""
    monkeypatch.setenv(SETTINGS_DIR_VAR, _empty_settings_dir)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every live provider call, so a test that reaches one fails.

    A test that drives the real translator down to a transport it supplies
    asks for :func:`supplied_transport`, which turns the refusal off for that
    test alone.
    """
    monkeypatch.setenv(OFFLINE_ENV, "1")


@pytest.fixture(autouse=True)
def _no_cloud_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the ``boto3`` credential chain off the metadata endpoints of an
    instance and of a container, so a test that finds no credential never
    sends a request off the machine."""
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    for var in (
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def supplied_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admit calls through the real translator to a transport the test supplies."""
    monkeypatch.delenv(OFFLINE_ENV)


@pytest.fixture
def moto_s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The S3 API, which moto serves in this process for one test.

    No request leaves the process, and ``boto3`` reads no configuration file
    and no profile of the machine. moto sets a fake key pair in the
    environment for the test.
    """
    from moto import mock_aws

    monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)
    for var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    with mock_aws():
        yield


@pytest.fixture
def no_static_key(moto_s3: None) -> Iterator[None]:
    """Remove the key pair that moto puts in the environment, and put it back
    before moto restores the environment."""
    names = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
    held = {name: os.environ.pop(name) for name in names if name in os.environ}
    yield
    os.environ.update(held)


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
