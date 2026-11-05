"""Narrow read-only compatibility for the audited OA 0020 -> 0021 expansion.

Backport this with its bootstrap call to every 0020 database consumer before
advancing the shared database. It neither runs nor stamps a migration. Unknown
successors still go through Alembic's normal rejection. A v1 Gateway rollback
also requires all v2 processing and callbacks to have drained.
"""

from __future__ import annotations

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import _normalize_default

BASE_REVISION = "0020_ppt_confirmation"
ADDITIVE_REVISION = "0021_oa_result_sets"


def accepts_oa_additive_schema(connection, *, local_head: str) -> bool:
    """Accept only the known successor with exactly the required column shapes.

    Called under the existing bootstrap lock, only for a versioned database.
    The current 0021 application and all other heads retain normal migration
    behavior; only backported 0020 consumers use this compatibility contract.
    """
    if local_head != BASE_REVISION:
        return False
    revisions = list(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalars())
    if revisions != [ADDITIVE_REVISION]:
        return False
    inspector = sa.inspect(connection)
    if not inspector.has_table("oa_policy_flow_jobs"):
        raise RuntimeError("oa_additive_schema_shape_mismatch")
    columns = {column["name"]: column for column in inspector.get_columns("oa_policy_flow_jobs")}
    protocol = columns.get("result_protocol", {})
    files = columns.get("result_files", {})
    if not (
        isinstance(protocol.get("type"), sa.String)
        and getattr(protocol.get("type"), "length", None) == 8
        and protocol.get("nullable") is False
        and _normalize_default(protocol.get("default")) == "'v1'"
        and isinstance(files.get("type"), sa.JSON)
        and files.get("nullable") is True
        and files.get("default") is None
    ):
        raise RuntimeError("oa_additive_schema_shape_mismatch")
    return True
