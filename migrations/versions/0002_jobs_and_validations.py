"""jobs and validations (Phase 2, ADR-0006). Additive only: new tables and nullable columns.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08 17:42:13.361748
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "validations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("features", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=True),
        sa.Column("manifest_sha", sa.String(length=64), nullable=False),
        sa.Column("run_timeout_s", sa.Double(), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("problems", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="validations_status"
        ),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["builds.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requires", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("validation_id", sa.BigInteger(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_sha", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'leased', 'succeeded', 'failed', 'dead')", name="jobs_status"
        ),
        sa.CheckConstraint(
            "type IN ('INSTALL_BUILD', 'RUN_TEST', 'GENERATE', 'SCORE', 'CRAWL')", name="jobs_type"
        ),
        sa.CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="jobs_attempts"),
        sa.ForeignKeyConstraint(
            ["validation_id"],
            ["validations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "jobs_claimable",
        "jobs",
        ["priority", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "jobs_leases",
        "jobs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'leased'"),
    )
    op.create_index("jobs_validation", "jobs", ["validation_id"], unique=False)
    op.add_column("candidates", sa.Column("idx", sa.Integer(), nullable=True))
    op.create_unique_constraint("candidates_run_idx", "candidates", ["generation_run_id", "idx"])
    op.add_column("executions", sa.Column("job_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("executions_job_id_fkey", "executions", "jobs", ["job_id"], ["id"])
    op.add_column("generation_runs", sa.Column("validation_id", sa.BigInteger(), nullable=True))
    op.add_column("generation_runs", sa.Column("job_key", sa.String(length=200), nullable=True))
    op.create_unique_constraint("generation_runs_job_key_key", "generation_runs", ["job_key"])
    op.create_foreign_key(
        "generation_runs_validation_id_fkey",
        "generation_runs",
        "validations",
        ["validation_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("generation_runs_validation_id_fkey", "generation_runs", type_="foreignkey")
    op.drop_constraint("generation_runs_job_key_key", "generation_runs", type_="unique")
    op.drop_column("generation_runs", "job_key")
    op.drop_column("generation_runs", "validation_id")
    op.drop_constraint("executions_job_id_fkey", "executions", type_="foreignkey")
    op.drop_column("executions", "job_id")
    op.drop_constraint("candidates_run_idx", "candidates", type_="unique")
    op.drop_column("candidates", "idx")
    op.drop_index("jobs_validation", table_name="jobs")
    op.drop_index("jobs_leases", table_name="jobs", postgresql_where=sa.text("status = 'leased'"))
    op.drop_index(
        "jobs_claimable", table_name="jobs", postgresql_where=sa.text("status = 'queued'")
    )
    op.drop_table("jobs")
    op.drop_table("validations")
