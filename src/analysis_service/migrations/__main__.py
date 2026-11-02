"""Migrate the database that ``ANALYSIS_DATABASE_URL`` names to the latest revision.

Run it as a deploy step, before the service starts::

    python -m analysis_service.migrations

It reads the same database settings as the service, so give it the user and
the credentials of the migration role. A setting it cannot run on, or a
database at a revision this build does not hold, ends in one line on stderr
and exit status 1.
"""

import os
import sys

from alembic.util import CommandError

from analysis_service.errors import config_refusal
from analysis_service.migrations import upgrade
from analysis_service.store_settings import StoreConfigError


def main() -> int:
    """Run the migration, or refuse the configuration in one line.

    Each database setting the migration reads raises a
    :class:`StoreConfigError` when it is unusable, and Alembic raises a
    :class:`~alembic.util.CommandError` for a revision the tree does not hold.
    """
    try:
        upgrade(os.environ)
    except (StoreConfigError, CommandError) as exc:
        print(config_refusal(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
