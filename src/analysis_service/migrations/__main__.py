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

from analysis_service.errors import config_refusal
from analysis_service.sql_store import RevisionNotHeld, migrate_database
from analysis_service.store_settings import StoreConfigError


def main() -> int:
    """Run the migration, or refuse in one line.

    A database at a revision the tree does not hold raises
    :class:`~analysis_service.sql_store.RevisionNotHeld`, which names the
    remedy, and the line is that message. Each database setting the migration
    reads raises a :class:`StoreConfigError` when it is unusable, and the
    line names the setting as a configuration refusal.
    """
    try:
        migrate_database(os.environ)
    except RevisionNotHeld as exc:
        print(exc, file=sys.stderr)
        return 1
    except StoreConfigError as exc:
        print(config_refusal(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
