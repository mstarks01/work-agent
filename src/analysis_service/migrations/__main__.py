"""Migrate the database that ``ANALYSIS_DATABASE_URL`` names to the latest revision.

Run it as a deploy step, before the service starts::

    python -m analysis_service.migrations
"""

import os

from alembic import command

from analysis_service.migrations import alembic_config
from analysis_service.sql_store import database_url

if __name__ == "__main__":
    command.upgrade(alembic_config(database_url(os.environ)), "head")
