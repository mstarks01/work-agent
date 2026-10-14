"""The schema of the ``sql`` job store, as an Alembic tree.

The service never migrates. An operator runs ``python -m
analysis_service.migrations`` as a deploy step, under a role that may change
the schema, and the service's startup refuses a database whose revision is
not :func:`head`. A migration changes the table structure only. It never
rewrites a stored report, checkpoint or sources object.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

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
