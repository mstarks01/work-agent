"""Create the tables of the sql job store: principals, jobs, job_events, usage_rows.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_JSON_COLUMNS = (
    "frameworks",
    "answers",
    "shown_early",
    "corrections",
    "draft",
    "skipped_early",
    "resumption",
    "carried",
    "checkpoint",
    "validation_issues",
    "certification",
    "unreported_nodes",
)


def upgrade() -> None:
    op.create_table(
        "principals",
        sa.Column("owner_id", sa.String(64), primary_key=True),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("sub", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("issuer", "sub"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(64),
            sa.ForeignKey("principals.owner_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("parent_id", sa.String(64), index=True),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("measured_tokens", sa.BigInteger()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parent_revision", sa.Integer()),
        sa.Column("system_name", sa.Text()),
        sa.Column("amends", sa.String(64)),
        sa.Column("error", sa.Text()),
        sa.Column("ask_questions", sa.Boolean(), nullable=False),
        *(sa.Column(name, sa.Text(), nullable=False) for name in _JSON_COLUMNS),
        sa.Column("sources_sha256", sa.String(64), nullable=False),
        sa.Column("report_sha256", sa.String(64)),
    )
    op.create_table(
        "job_events",
        sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("event", sa.Text(), nullable=False),
    )
    op.create_table(
        "usage_rows",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(64),
            sa.ForeignKey("principals.owner_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("measured_tokens", sa.BigInteger()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("parent_id", sa.String(64), index=True),
    )


def downgrade() -> None:
    for table in ("usage_rows", "job_events", "jobs", "principals"):
        op.drop_table(table)
