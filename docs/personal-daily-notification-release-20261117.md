# Personal Work daily HTML link — Gateway 420

The personal scheduled energy daily report sometimes sent a generic completed
title, timestamp and chat link even when the report's final answer contained
the authenticated HTML reader link. A peer scheduler could reconcile the run
as successful before its final `last_ai_message` write, enqueue an empty result
snapshot, and send that snapshot through the generic formatter. The observed
November 16 notice was sent at 08:13:17.257258Z; the final result snapshot was
updated at 08:13:17.952533Z. The encrypted outbox payload had already been
cleared. The user's actual generic notice, bounded read-only observations and
real SQLite repository reproduction support this timing diagnosis; replaying
the final formatter is not evidence of the already-transmitted payload.

Gateway 420 changes only `work_notification_delivery.py`. Before sending a
notice that lacks a valid daily reader link, it reads one bounded result for
the same task occurrence, thread, run and owner. All run metadata associations
must match. The original Slash input identifies an energy daily task even if
its description is later edited. If the final reader link is not available,
the existing bounded pre-send retry path waits rather than sending a generic
success template. A ready link is the entire Work Markdown message:

```text
[点击查看日报网页](https://<approved-platform>/reports/energy-daily/<current-thread>/<actual-date>.html)
```

The URL must be present in the persisted final result and pass the existing
same-thread, approved-origin, HTTPS and real-date validation. No URL is guessed
from the clock or task title. The existing task/connection/binding guards,
recipient, delivery UUID, encrypted outbox schema and uncertain-send policy
remain in place. Ordinary task message bodies retain their generic formatting.

## Released scope and evidence

- Parent: merged PR #52, commit `a2148ed200b211a4e57832990390d9d4bc48b406`.
- Gateway: generation 419 → 420; UID unchanged. Final spec SHA256:
  `d4b61d0bc09cda8550f3d1564a899cb8d44e17033ae9bf58afb1147268c238a9`.
- Deployed sender: 8,848 bytes, SHA256
  `7eb938e2a77dcbb708e8177237be282687f2f202ff41caee015194e6560a1b5c`.
  Exact bytes are retained in the new source archive; the canonical backend
  copy has only equivalent formatting/import ordering.
- Activation: operation `55f53add31c741b5b859ba0dba3c61ec`.
  The archived 3,517-byte activation report matches the operator's original
  report SHA256 `3f213d29113448de68fde26944a5e4fb7ff0e9ac9ed7682e8d2f8236460c2e0b`.
- November 17 read-only acceptance: 70/70 updated, ready and available; no
  terminating replicas; 17 source files per Pod and Service backends verified.
- The operator subsequently confirmed: “已确认个人 Work 只收到‘点击查看日报网页’的 HTML 链接”.
  This is a direct operator business acceptance, not a tool-generated send.
  No new business-run ID was supplied; the failed historical chat is not
  relabeled as a successful run.

Concurrency remains 32, lease-aware multi-instance mode remains enabled and
both recursion budgets remain 10000. The Skill, group webhook path, Nginx,
credentials, resources and other deployments are unchanged. This GitHub sync
does not connect to production, deploy, replay tasks or send messages.

The 419 OOM/restart evidence remains immutable. The successful 420 fleet check
does not establish that incident's cause or benchmark 32-slot throughput.
The verifier checks mounted source/config identity rather than introspecting
loaded application modules. The operator's later single-link receipt provides
the separate business acceptance.

## Verification and historical parents

Run with Python 3.12+:

```bash
python deploy/enterprise/verify-personal-notification-source-20261117.py --ref HEAD
python deploy/enterprise/verify-post51-energy-source-20261116.py --ref HEAD
python deploy/enterprise/verify-release-source-20261023.py --ref HEAD
```

The new seal qualifies the exact 420 sources and receipts before exposing
finite archived 419 parent files to historical verifiers. Old inventories and
receipts retain their original acceptance flags. The standalone November 16
package's `production_executed: false` records its preparation time; later
operator receipts are separate evidence. Archived activation commands are for
provenance only and must not be rerun as part of review.

Local validation before the operator's rollout included 41 deployment-boundary
cases on Windows and Linux, 13 real SQLite repository cases on Windows and
ARM64, actual Python 3.7 sealed-resource/wrong-host guards, PostgreSQL query
compilation, and 10 audited notification cases for both ARM64 preflight and
postflight. Offline cached-image library inventories and synthetic fixtures
are explicitly recorded separately from fixed production version pins.
