"""Add opt-in OA v2 result sets; retain columns during application rollback."""

import sqlalchemy as sa
from alembic import op

revision = "0021_oa_result_sets"
down_revision = "0020_ppt_confirmation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("oa_policy_flow_jobs")}
    if "result_protocol" not in columns:
        op.add_column("oa_policy_flow_jobs", sa.Column("result_protocol", sa.String(8), nullable=False, server_default="v1"))
    if "result_files" not in columns:
        op.add_column("oa_policy_flow_jobs", sa.Column("result_files", sa.JSON(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("OA result manifests must be retained; drain v2 jobs and roll back application only")
