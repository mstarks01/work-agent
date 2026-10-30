"""Migrate the database that ``ANALYSIS_DATABASE_URL`` names to the latest revision.

Run it as a deploy step, before the service starts::

    python -m analysis_service.migrations

It reads the same database settings as the service, so give it the user and
the credentials of the migration role.
"""

import os

from analysis_service.migrations import upgrade

if __name__ == "__main__":
    upgrade(os.environ)
