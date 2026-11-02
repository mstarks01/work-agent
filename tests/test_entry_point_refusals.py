"""Each command-line entry point refuses a configuration it cannot run on in one line.

The harness loads the resilience file to bound each corpus source, and a
replay loads the model tiers. A :class:`~analysis_service.errors.ConfigError`
from either names the file or the variable to fix, and a traceback over it
hides that line. ``tests/test_evals_run.py`` holds the same refusal for
``run.main``.

The migration step reads the database settings, and the service's startup
tells an operator to run it. A store setting it cannot run on, or a database at
a revision this build does not hold, ends in the same one line.
:data:`SERVICE_ENTRY_POINTS` holds each ``python -m analysis_service...``
command to a stated answer.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from analysis_service.deployment import RESILIENCE_VAR
from analysis_service.migrations.__main__ import main as migrations_main
from analysis_service.store_settings import DATABASE_URL_VAR
from evals import build_diagrams
from evals.critic_review.__main__ import main as critic_review_main
from tests.factories import TEST_TIER_ENV
from tests.source_tree import PYTHON, module_name, parse, source_files, without
from tests.sql_stores import migrate, sqlite_url
from tests.test_evals_assembly import OLD_BASELINE
from webapp import offline_sitting, review, sitting

pytestmark = pytest.mark.lint

REFUSAL = "cannot run on this configuration: "


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
    assert printed.startswith(REFUSAL)
    assert str(missing) in printed
    assert printed.count("\n") == 1


def test_the_critic_review_refuses_tiers_that_select_no_vendor(monkeypatch, capsys):
    for var in TEST_TIER_ENV:
        monkeypatch.delenv(var)

    assert critic_review_main(["--accept-cost", "0"]) == 1
    printed = capsys.readouterr().err
    assert printed.splitlines()[-1].startswith(REFUSAL)
    assert "no vendor selected" in printed
    assert "Traceback" not in printed


#: Each database setting the migration step cannot run on, the value that
#: spells it, and what the refusal names. No refusal repeats the value.
BROKEN_DATABASES: dict[str, tuple[str, str]] = {
    "unset": ("", f"set {DATABASE_URL_VAR}"),
    "an unknown driver": ("mysql://host/jobs", "unknown value for the driver"),
    "a relative SQLite path": ("sqlite+aiosqlite:///jobs.db", "absolute path"),
}


@pytest.mark.parametrize("name", sorted(BROKEN_DATABASES))
def test_the_migration_step_refuses_a_store_setting_in_one_line(
    name, monkeypatch, capsys
):
    value, named = BROKEN_DATABASES[name]
    monkeypatch.setenv(DATABASE_URL_VAR, value)

    assert migrations_main() == 1
    printed = capsys.readouterr().err
    assert printed.startswith(REFUSAL)
    assert named in printed
    assert not value or value not in printed
    assert printed.count("\n") == 1


def test_the_migration_step_refuses_a_revision_this_build_does_not_hold(
    tmp_path, monkeypatch, capsys
):
    database = tmp_path / "jobs.db"
    migrate(database)
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE alembic_version SET version_num = 'r9999'")
    engine.dispose()
    monkeypatch.setenv(DATABASE_URL_VAR, sqlite_url(database))

    assert migrations_main() == 1
    printed = capsys.readouterr().err
    assert printed.startswith(REFUSAL)
    assert "r9999" in printed
    assert str(tmp_path) not in printed
    assert printed.count("\n") == 1


def test_the_migration_command_exits_1_with_one_line(tmp_path):
    """The command as an operator runs it, so the ``__main__`` guard is read."""
    done = subprocess.run(
        [sys.executable, "-m", "analysis_service.migrations"],
        env={"PATH": "", "PYTHONPATH": ":".join(sys.path), DATABASE_URL_VAR: ""},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 1
    assert done.stderr == f"{REFUSAL}set {DATABASE_URL_VAR} for the sql job store\n"


#: Each ``python -m analysis_service...`` command, and how it meets a
#: configuration it cannot run on. A new command needs a row.
SERVICE_ENTRY_POINTS: dict[str, str] = {
    "analysis_service.migrations": (
        "refuses an unusable database setting and a revision this build does"
        " not hold in one line; the tests above drive each"
    ),
    "analysis_service.smoke": (
        "run_smoke raises on a broken deployment by design: its docstring says"
        " an unreadable or invalid config file is not a fact about a provider"
    ),
    "analysis_service.conformance": (
        "reads no setting: it profiles the constant REFERENCE_MODELS table"
        " against the pinned LiteLLM model map"
    ),
    "analysis_service.verify_report": (
        "reads no setting: its inputs are named files, and it refuses an"
        " unreadable keyring, report or attestation in one line with exit 2"
    ),
    "analysis_service.frameworks.asvs.roster": (
        "reads no setting: it rewrites the ASVS lane skills from the catalog"
        " the package holds"
    ),
}

#: The roots the table leaves out. Each of their commands is a maintainer tool
#: that the harness or a sitting runs, and the tests above drive the ones that
#: load configuration.
_NOT_SERVICE_COMMANDS = {
    "webapp": "the review and sitting apps, which a maintainer runs from a checkout",
    "evals": "the eval harness and its tools, which a maintainer runs from a checkout",
    "examples": "example clients of the HTTP API, which read no service setting",
    ".github/scripts": "the scripts CI runs over a pull request, not service commands",
}


def _command(path: Path) -> str | None:
    """The ``python -m`` name of the file at ``path``, or None where it is no
    command."""
    if path.name == "__main__.py":
        return module_name(path.parent)
    guard = ast.parse('__name__ == "__main__"', mode="eval").body
    for node in parse(path).body:
        if isinstance(node, ast.If) and ast.dump(node.test) == ast.dump(guard):
            return module_name(path)
    return None


def test_each_service_command_states_how_it_meets_a_broken_configuration():
    files = without(source_files(*PYTHON), _NOT_SERVICE_COMMANDS)
    commands = {name for path in files if (name := _command(path))}

    assert commands == SERVICE_ENTRY_POINTS.keys()
