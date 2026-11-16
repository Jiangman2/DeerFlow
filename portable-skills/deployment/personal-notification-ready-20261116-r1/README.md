# Personal daily notification readiness (not deployed)

The operator's notification for chat
`9a762837-e4c4-4376-a14d-fcefbacd4bbc` contains only the generic completed
title, completion time and conversation URL. It does not contain the intended
HTML-reader link. This was a scheduled 2.1.7 run (13:35–13:40 Asia/Shanghai),
before the 2.1.8 installation began at about 13:41:33. The personal outbox
reports sent and has already cleared its ciphertext, so source-function replay
cannot establish what was actually transmitted.

The field timing is consistent with an early completion snapshot: the outbox
was enqueued at 13:40:46.871, while the run's final completion write is timestamped
13:40:47.646. The field-matching scheduler service, RunManager and worker hashes
agree with the retained source. Real isolated SQLite repository execution
reproduces the race: reconcile_active_runs sees an underlying success before
its final last_ai_message is stored and enqueues recovered=True with empty
result_text. Its generic formatter exactly reproduces the reported template.
Later source replay sees the now-populated result and yields the desired link,
which explains why the earlier diagnostic did not reproduce the actual notice.

The candidate changes one Gateway delivery module only. When an already queued
notification does not resolve to a valid single daily-reader link, the sender
reads a bounded result snapshot for that exact task occurrence, run, thread and
owner. It uses the persisted original input to recognize an explicit
/energy-market-daily task even after the current task description changes.
All run metadata associations must match. A missing reader link waits through
the existing bounded pre-send retry path; it cannot become a generic successful
chat-link notice for this daily task. Unrelated task message bodies keep the
existing generic formatting. Already-valid link notices keep their fast path.

The existing outbox namespace, encrypted payload schema, connection/binding
rechecks, recipient, delivery UUID, Work Markdown rendering and uncertain-send
policy are unchanged. No new database column, migration, Skill change, Nginx
change, budget change, task replay or group webhook send is included. The data
refresh makes one indexed, parameter-bound SELECT, limits the row count to one,
and bounds the returned prompt/input/answer projections. It performs no web
search and does not construct a report URL from the clock or task title.

Validation: Windows/Python3.12 and cached production ARM64 each passed all 13
SQLite/source-function tests, including the real repository race, delayed
result readiness, same delivery UUID, wrong-owner/run/thread rejection,
untrusted reader URLs and uncertain external-send behavior. Transport calls
are mocked; no real bot message or production database request was made.
The refresh query is separately compiled for PostgreSQL. Source F/E9 lint passes.

This is a reviewed local candidate, not a production rollout. Gateway 419's
32-concurrency update reached 70 updated/ready/available Pods but its all-Pod
acceptance stopped on a historical restart. The operator identified
gateway-747b86d58b-s6mfw as OOMKilled/137 at 14:17:53, restarted at 14:17:54 and
currently ready. Resource limits/current metrics are being checked. Do not
weaken the restart guard, delete the Pod to erase history, replay activation,
or layer another Gateway cutover before the observed memory issue is assessed.

The newly reported server web-search outage is a separate possible report-news
failure. Its failing chat/error is still required; do not attribute this
already accepted four-artifact run's personal notification shape to search.
