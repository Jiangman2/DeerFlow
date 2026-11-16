"""Race the field-matching repository against one local SQLite database."""
import asyncio
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
SOURCE=ROOT/'backend'
sys.path[:0]=[str(SOURCE/'packages/harness'),str(SOURCE)]
from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.scheduled_task_runs import ScheduledTaskRunRepository
import deerflow.persistence.scheduled_task_runs.sql as source_module

assert hashlib.sha256(Path(source_module.__file__).read_bytes()).hexdigest()=='7c056874bcbdcede1743fbe55b498f2df5a7d9d12cdea3a38af03703d9462281'


async def main():
    with tempfile.TemporaryDirectory(prefix='scheduler-32-local-db-') as temporary:
        postgres=sys.argv[1:]==['--postgres-socket','/scheduler-pg']
        assert not sys.argv[1:] or postgres,'Only the isolated local Unix-socket test database is permitted'
        config=DatabaseConfig(backend='postgres',postgres_url='postgresql+asyncpg://postgres@/postgres?host=/scheduler-pg') if postgres else DatabaseConfig(backend='sqlite',sqlite_dir=temporary)
        await init_engine_from_config(config)
        try:
            factory=get_session_factory()
            repos=[ScheduledTaskRunRepository(factory) for _ in range(4)]
            now=dt.datetime(2026,11,16,1,tzinfo=dt.timezone.utc)
            for index in range(43):
                await repos[0].create(run_record_id='occurrence-'+str(index),task_id='task-'+str(index),
                                      thread_id='thread-'+str(index),scheduled_for=now,trigger='manual',
                                      status='running' if index<3 else 'queued')
            await asyncio.gather(*(repo.count_active_runs() for repo in repos*4))
            claims=await asyncio.gather(*(
                repos[index%4].claim_queued_run('occurrence-'+str(index),lease_owner='worker-'+str(index%4),
                                               now=now,lease_seconds=120,global_max_concurrent_runs=32)
                for index in range(3,43)))
            accepted=sum(item is not None for item in claims)
            total=await repos[0].count_active_runs()
            assert accepted==29 and total==32,(accepted,total)
            print(json.dumps({'status':'FIELD_SOURCE_GLOBAL_BUDGET_32_LOCAL_DATABASE_RACE_PASSED',
                              'already_running':3,'concurrent_claimants':40,'repository_instances':4,
                              'new_claims_accepted':accepted,'global_executing_total':total,
                              'remaining_queued':40-accepted,'database_backend':'postgres' if postgres else 'sqlite',
                              'postgresql_race_exercised':postgres,
                              'production_database_access':False,'provider_requests':0,'work_messages_sent':0}))
        finally:
            await close_engine()


if __name__=='__main__':asyncio.run(main())
