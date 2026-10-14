"""Migrate the database that ``ANALYSIS_DATABASE_URL`` names to the latest revision.

Run it as a deploy step, before the service starts::

    python -m analysis_service.migrations

It reads the same database settings as the service, so give it the user and
the credentials of the migration role.
"""

import os

from alembic import command

from analysis_service.migrations import alembic_config

if __name__ == "__main__":
    command.upgrade(alembic_config(os.environ), "head")
