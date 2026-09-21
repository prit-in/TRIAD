"""Record which stage an execution belongs to.

`executions.task_id` is null whenever the pipeline runs directly rather than through the task
queue, which is what `scripts/run_demo.py` does. The run detail's stage list was built from
`agent_tasks` alone, so those runs rendered an empty timeline while having executed four
stages. Nullable, because every row written before this migration has no stage recorded and
guessing one from the command string would be inference dressed as data.

Revision ID: b1f2a3c4d5e6
Revises: 244c0cc68301
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1f2a3c4d5e6"
down_revision: str | None = "244c0cc68301"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("stage", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("executions", "stage")
