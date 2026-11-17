# Personal daily notification readiness (Gateway 420 accepted November 17)

The operator deployed this sender on November 16, verified all 70 Gateway 420
Pods and Service backends on November 17, then confirmed that personal Work
receives only the HTML reader link. See
`docs/personal-daily-notification-release-20261117.md` and the separate operator
receipts under `release/energy-personal-notification-ready-20261116-r1/`.
Preparation manifests retain their original pre-deployment flags. Commands
below are archived provenance and must not be rerun during review.

The November 16 16:25 operator read-only inspection also confirms the current
Gateway is generation 419, spec
`08a8afffb65eaca6259fe8b01111a5c68a2523fba639f17a4b25611e9ccb98c8`.
For chat `a5f059b0-1858-4687-8d16-2de8f853c418`, the native personal outbox
is sent at 08:13:17.257258Z; the final run snapshot was updated at
08:13:17.952533Z. The stored final result contains the actual current-thread
HTML reader link. The notification modules still have the old 418-parent
hashes. The already-cleared ciphertext was not read; the operator's actual
generic message, recorded timing, and isolated reproduction support the same
early-snapshot diagnosis. This is not proof that the already-sent ciphertext
was replayed. The current task is paused; the release never unpauses it.

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

The preparation checks below preceded the operator's accepted 420 deployment.
The original Gateway 419 incident remains separate historical evidence. Its
32-concurrency update reached 70 updated/ready/available Pods but its all-Pod
acceptance stopped on a historical restart. The operator identified
gateway-747b86d58b-s6mfw as OOMKilled/137 at 14:17:53, restarted at 14:17:54 and
currently ready. Its request is 3 GiB and limit 4 GiB; Metrics API is unavailable.
The later observation has 70 updated/ready/available replicas. This one-file
release preserves resources, concurrency and all scheduler configuration; it
does not resolve or claim acceptance of that earlier OOM incident. Its own new
fleet acceptance still rejects restarts. Never delete a Pod to erase history
or replay an activation to bypass a failed guard.

The newly reported server web-search outage is a separate possible report-news
failure. Its failing chat/error is still required; do not attribute this
already accepted four-artifact run's personal notification shape to search.

## Scoped operator

The operator is derived from the previously field-qualified r2 concurrency
release, with the original CAS, projected-Pod/defaulting, endpoint ownership,
source closure, private journal and read-only recovery guards. It binds the
accepted concurrency PLAN hash and preserves its exact config bytes. A new
immutable ConfigMap contains only `work_notification_delivery.py`. The existing
read-only delivery mount is repointed to that ConfigMap through a new volume
with explicit defaultMode 420. Restoring that one mount and removing that one
volume must reproduce the complete original Deployment spec. Four JSON Patch
tests cover UID, resourceVersion, generation and original spec before the
single spec replacement. Old ConfigMaps and the other notification module
remain unchanged. Activation returns after submitting one patch; the separate
read-only entry checks progress and eventually all 70 Pods and Service backends.

The preflight and final child check 17 source files, exact preserved config,
fixed production library versions, and ten isolated notification/query cases.
The child imports no application config, models or sender modules and creates
no database connection; isolated SQL-role adapters compile the same expression.
The real ORM/repository race remains covered by 13 SQLite tests on Windows and
ARM64. The run store itself caps `last_ai_message` at 2000 characters, matching
the refresh query's bound.

Local operator tests: Windows Python 3.12 and Linux Python 3.10 / UID 1000 each
pass 41 cases. Python 3.7 verifies both sealed resources and both wrong-host
zero-command entry guards. Offline ARM64 preflight and postflight each pass
ten audited cases against exact retained Python sources. Those offline images
have library versions 1.3.14 / 1.4.9 / 1.2.9, supplied only as a local fixture;
the sealed production qualifier retains the previously accepted version pins
1.2.15 / 1.3.3 / 1.1.9 and refuses other versions. The offline OpenSSL inventory
entry and YAML are synthetic; production source/config hashes remain fixed.
No real database, task or Work send is used in these checks.

Historical activation invocation (already completed; do not replay):

    sudo python3 -I -B -u /home/cnooc/activate-energy-personal-notification-20261116-r1.py --activate-personal-notification-once

Historical read-only follow-up (the November 17 acceptance is already archived):

    sudo python3 -I -B -u /home/cnooc/activate-energy-personal-notification-20261116-r1.py --verify-readonly

Do not replay already-sent old occurrences. Business acceptance was supplied separately by the operator after the rollout;
the specific new business-run ID was not supplied.
