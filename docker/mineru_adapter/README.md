# Enterprise MinerU Adapter

The worker serializes native parsing through `ParseCoordinator`. The dispatcher
is the same image in a different role. Do not increase in-process native parser
concurrency; scale independent workers only after a CPU/memory/storage review.

## Dedicated-service asynchronous routes (candidate, 2026-10-14)

Keep `MINERU_REQUEST_MODE=async`. `MINERU_API_URL` remains the service base
(or its `/tasks` / `/file_parse` endpoint). The client submits exactly once to
the locally constructed `/tasks` endpoint.

Optional **operator-owned** `MINERU_TASK_QUERY_URL` is the complete query
collection URL, including its final `/tasks`. The client adds `/{task_id}` for
status and `/{task_id}/result` for results. An unset/empty value retains the old
contract. An explicit value must be an HTTPS URL on the submission endpoint's
origin; credentials, query strings, fragments, traversal and escaped paths are
rejected. HTTP redirects are not followed. Provider-returned URLs never select
an authenticated destination.

Reviewed candidate settings, not a live production change:

```text
MINERU_API_URL=https://aiapi.cnooc.com.cn/MinerU2.5-2605-1.2B/FC-02
MINERU_TASK_QUERY_URL=https://aiapi.cnooc.com.cn/MinerU2.5-2605-1.2B/FC-02/dynamic/member1/mineru-for-zy-agent/tasks
MINERU_REQUEST_MODE=async
```

Credentials stay in the runtime `deer-flow-mineru` Secret, never source,
images, command arguments or logs. Preserve direct routing, explicit enterprise
CA verification, the shared deadline and bounded same-task polling retries.
An uncertain POST is never automatically resubmitted or changed into a sync
request. Rolling activation requires draining active work and a reviewed
rollback plan; changing a Secret does not update existing process environments.

## Performance acceptance

The goal is both faster scanned-document knowledge ingestion and lower chat
upload latency. A concurrency target of 64 is not proof of either. Review worker
resource requests/limits, actual memory peaks, ready endpoints, dispatcher
capacity and WeKnora core/shared admission together. Preserve the independent
post-processing/enrichment limits. Keep chat priority and measure interactive
latency while bulk ingestion is active. Do not run 64 native parses in one
process or treat the dispatch queue length as the parse concurrency.

Release preparation and read-only capacity audit:
`release/mineru-provider-switch-20261014/ASYNC-PLAN.txt`.
