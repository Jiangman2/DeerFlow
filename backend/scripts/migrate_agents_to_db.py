#!/usr/bin/env python
"""One-shot importer: copy file-backed custom agents into the ``db`` agent store.

For operators switching ``agent_storage.backend`` from ``file`` to ``db``. Reads
every agent from the on-disk layout (both the per-user
``{base_dir}/users/{user_id}/agents/`` and the legacy shared
``{base_dir}/agents/``, with the same shadowing rule the file store uses) and
writes each as a row in the shared ``agents`` table.

Design (mirrors ``scripts/migrate_user_isolation.py``):
- Explicit, operator-run. Nothing auto-imports on boot.
- Idempotent: an agent already present in the db is skipped, so re-running is safe.
- Non-destructive: the on-disk files are left untouched, so unsetting
  ``agent_storage.backend`` (back to ``file``) is a clean rollback.

Usage::

    python scripts/migrate_agents_to_db.py [--dry-run]

Requires ``database.backend`` to be ``sqlite`` or ``postgres`` in config.yaml.
"""

from __future__ import annotations

import argparse
import logging
import sys

from deerflow.config.app_config import get_app_config
from deerflow.persistence.agents.base import AgentExistsError
from deerflow.persistence.agents.file import FileAgentStore
from deerflow.persistence.agents.model import AgentRow
from deerflow.persistence.agents.sql import SqlAgentStore, get_sync_sessionmaker

logger = logging.getLogger("migrate_agents_to_db")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import file-backed custom agents into the db agent store.")
    parser.add_argument("--dry-run", action="store_true", help="List what would be imported without writing to the database.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    config = get_app_config()
    db_backend = getattr(config.database, "backend", None)
    if db_backend not in ("sqlite", "postgres"):
        logger.error(
            "database.backend is %r; this importer needs 'sqlite' or 'postgres'. Set it in config.yaml (the same database the gateway uses).",
            db_backend,
        )
        return 1

    source = FileAgentStore()
    agents = source.list_all()
    if not agents:
        logger.info("No file-backed agents found — nothing to import.")
        return 0

    if args.dry_run:
        for user_id, cfg in agents:
            logger.info("[dry-run] would import %s/%s", user_id, cfg.name)
        logger.info("[dry-run] %d agent(s) would be imported. Source files are left in place.", len(agents))
        return 0

    # This one-shot importer must also work as a self-contained hotfix against
    # an older enterprise migration chain. Create only its owned table here;
    # normal full releases still create it through Alembic migration 0019.
    # ``checkfirst`` makes this idempotent and avoids applying unrelated schema
    # revisions merely to import Digital Employees.
    sync_url = config.database.app_sync_sqlalchemy_url
    session_factory = get_sync_sessionmaker(sync_url)
    with session_factory() as session:
        AgentRow.__table__.create(bind=session.get_bind(), checkfirst=True)

    dest = SqlAgentStore(sync_url)
    imported = 0
    skipped = 0
    for user_id, cfg in agents:
        soul = source.get_soul(cfg.name, user_id=user_id) or ""
        # exclude_unset keeps the stored document as sparse as the source file
        # (only the keys the operator actually wrote), matching the file layout.
        document = cfg.model_dump(exclude_unset=True)
        try:
            dest.create(cfg.name, document, soul, user_id=user_id)
            imported += 1
            logger.info("imported %s/%s", user_id, cfg.name)
        except AgentExistsError:
            skipped += 1
            logger.info("skip %s/%s: already present in db", user_id, cfg.name)

    logger.info("Done: %d imported, %d already present. Source files left in place (rollback: revert agent_storage.backend to 'file').", imported, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
