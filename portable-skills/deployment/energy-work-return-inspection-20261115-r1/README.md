# Missing Work notification — fixed-thread read-only inspection

The user identified conversation `aa98356f-e332-4019-9d68-0d1e79c30ed7` as a
successful report with no Work return. This tool has no retry, send, update,
rollout or schedule mutation mode. It reads only this named conversation's two
latest runs, two scheduled occurrences and their owner-bound notification and
connection metadata. It neither reads notification ciphertext nor credentials
from Kubernetes Secrets or channel-credential tables. Application configuration
can resolve the existing database credential internally to make read-only SQL
queries; neither that configuration nor credentials are exported.

The host uses a projected Gateway Pod list and verifies one owned ready Pod
before running the sealed child. It checks stable Pod/Deployment identities
afterward. The child enforces a database-only network audit, no filesystem
writes/processes/locks, default and explicit read-only transactions, per-query
deadlines, bounded event/result text reads and a 60-second total deadline.

Only qualified Gateway 418 notification source hashes are accepted for replay.
The actual formatter functions are extracted and executed in isolation against
the stored result and recent final AI messages. No sender or application startup
is imported. Exported data consists of statuses, timestamps, sizes, hashes and
validated current-task public reader URLs, never report/news text or webhook
addresses. The current outbox clears failed payloads and does not persist sender
error reasons, so queue state alone cannot establish a specific provider error.
Replay is a diagnostic of the stored result, not proof of an actual send.

Windows and Linux/Python 3.10/UID 1000 each pass ten focused tests. They include
the reproducible case where a valid standalone reader link followed by Chinese
punctuation produces an empty notification under the current strict parser.
That is a confirmed format defect but has not yet been established as this
conversation's failure cause. Read the operator's diagnostic before changing
production. Do not resend or replay the completed task for inspection.
