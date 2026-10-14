"""Rename the column jobs.round_revision to jobs.revision.

Revision ID: 0002
Revises: 0001

SQLite and PostgreSQL both rename a column in place. A copy of the table, as
a batch operation makes on SQLite, would fail on the foreign key of
``job_events``.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs RENAME COLUMN round_revision TO revision")


def downgrade() -> None:
    op.execute("ALTER TABLE jobs RENAME COLUMN revision TO round_revision")
