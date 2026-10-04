"""Approvals that need several people, and the policy action each approval decides.

PRD section 30 makes "promotion despite a failed check" an action needing two approvers - an ML
Engineer *and* an Admin - with a written override note, and `governance.yaml` says so with
`approvers_all_of`. Nothing could express it: an approval row had one decider and no record of
which policy action it decided, so `decide()` mapped every `model_promotion` row back to
`model.promote` and treated `approvers_all_of` as "any one of".

- `policy_action`: the governance action this row decides. NULL on older rows, which keep the
  mapping from `action_type`.
- `required_role`: for one share of an all-of approval, the only role that may decide it.
- `group_id`: the rows that together are one decision; every one of them must approve.

Revision ID: d8e4f0a26c53
Revises: c7d3e9a15b42
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8e4f0a26c53"
down_revision: str | None = "c7d3e9a15b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("policy_action", sa.String(length=64), nullable=True))
    op.add_column("approvals", sa.Column("required_role", sa.String(length=32), nullable=True))
    op.add_column("approvals",
                  sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_approvals_group_id", "approvals", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_approvals_group_id", table_name="approvals")
    op.drop_column("approvals", "group_id")
    op.drop_column("approvals", "required_role")
    op.drop_column("approvals", "policy_action")
