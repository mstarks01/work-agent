"""The schema of the ``sql`` job store, as an Alembic tree.

The service never migrates. An operator runs ``python -m
analysis_service.migrations`` as a deploy step, under a role that may change
the schema, and the service's startup refuses a database whose revision is
not :func:`head`. The first-run app migrates a SQLite database itself, because
one person owns the file and runs the app (ADR 0110). A migration changes the
table structure only. It never rewrites a stored report, checkpoint or
sources object.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

#: Alembic's script directory. It is not a package, so nothing imports its
#: ``env.py``, which runs a migration when Alembic loads it.
_SCRIPTS = str(Path(__file__).parent / "scripts")


def alembic_config(env: Mapping[str, str]) -> Config:
    """The Alembic configuration that migrates the database the settings name.

    The migration connects with the database settings of the service, so it
    uses the same TLS and credential rules.
    """
    config = Config()
    config.set_main_option("script_location", _SCRIPTS)
    config.attributes["env"] = env
    return config


def head() -> str | None:
    """The latest revision of the tree: the one the service needs."""
    return ScriptDirectory(_SCRIPTS).get_current_head()


def holds(revision: str | None) -> bool:
    """Whether the tree holds ``revision``. ``None`` is a database that no
    migration touched, which each tree holds."""
    if revision is None:
        return True
    return any(
        script.revision == revision
        for script in ScriptDirectory(_SCRIPTS).walk_revisions()
    )


def upgrade(env: Mapping[str, str]) -> None:
    """Migrate the database that the settings name to :func:`head`.

    Alembic's script runs an event loop of its own, so call it where none
    runs.
    """
    command.upgrade(alembic_config(env), "head")
