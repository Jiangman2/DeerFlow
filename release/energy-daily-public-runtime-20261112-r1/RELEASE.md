# Energy daily public-mount repair, November 12

The operator's 09:41 Beijing receipt confirms Skill 2.0.2 was published and the
ordinary-category mount check produced the fixed PNG as UID/GID 1000 in 3.764
seconds. The initial existing-Sandbox visibility check returned a redacted
`Refused` result. At 10:14 Beijing a separate read-only postcheck passed in an
ordinary Sandbox on node 0011: the public category mount was read-only, the
107-file Skill tree and runtime manifest matched, UID/GID were 1000, and `/tmp`
was writable without `noexec`. The prior refusal's exact cause was not
reconstructed; do not infer it from the new successful result. See
`FIELD-PUBLIC-REPAIR.json` and `FIELD-POSTCHECKS-20261112.json`; these summarize
pasted output and are not downloaded raw receipts. This release does not
require another Gateway rollout, and its claimed installer must not be rerun.

The operator's ordinary Sandbox snapshot shows the Skills PVC mounted only at
`/mnt/skills/public` with `subPath: public`. The previous browser-fix release
placed its runtime beside `public`, outside that view. Its maintenance check
added a separate runtime mount, so that successful PNG check did not prove
ordinary Sandbox visibility. Preserve the earlier receipt as historical
evidence; do not reinterpret it as a successful real-conversation check.

The new installer preserves that old runtime and copies its verified contents
under `public/.energy-browser-runtime-20261112-r1/linux-arm64`. A one-parent
binding keeps the dependency inside the existing ordinary public view. The
pre-publication PNG check uses UID/GID 1000 and only the real public category
mount plus the candidate Skill leaf, without a separate browser-runtime mount.
After publication it checks visibility in an existing ordinary Sandbox. It
does not patch Gateway, Provisioner, credentials, existing Sandboxes, or history.

Skill 2.0.2 also checks known environment prerequisites before collection and
publishes one complete generation of PNG, HTML, JSON, and text only after the
PNG succeeds. A failed build preserves prepared inputs and the prior accepted
generation. The business parsing, quote date, units, research, and report
renderer remain unchanged; missing-price wording is neutral rather than
claiming an unproven supplier publication delay. See `CODE-PACKAGE.json` for
sealed package provenance.

The named-thread probe at 09:33 Beijing did not find a Ready Sandbox carrying
the exact thread annotation. This does not prove reclamation because legacy
Pods can lack that annotation. It did not inspect persisted inputs or establish
the cause of partial JKM-only quotes. A new persistent-storage inspection must
resolve the trusted owner through read-only metadata, validate the actual
user-data mount, and read only that thread's bounded market/project files.
It must not recreate the Sandbox or request new quotes. The 10:14 persistent
inspection then succeeded through the trusted owner and actual Gateway
user-data PVC. The saved project was prepared on November 12 at 08:06 Beijing
for publication November 12 and market date November 11. Its target response
had success code zero and all 14 correctly dated rows: six oil/gas prices were
positive numbers and eight freight rows contained recognized missing markers.
There were no recorded acquisition failures and the market input hash matched
the project manifest. This does not support missing credentials, network
failure, or dropped numeric values as the cause of this saved partial report.
It does not establish why the supplier freight fields were missing or recover
an earlier JKM-only snapshot. Missing freight must not be relabeled as a
holiday or filled with old prices.

The inspected project outputs contained HTML, JSON, and text; the inspected
thread outputs had no matching published daily files. Generation subdirectories
were explicitly outside the inspection scope, so those observations do not
prove universal absence of artifacts. Retry only the original project's build
and normal acceptance/delivery to validate PNG recovery without reacquisition.
A new project is needed to test a new collection; repeated prepare for the same
project intentionally reuses the saved 08:06 snapshot.

Local validation covers 76 Skill tests under Python 3.10, UID 1000, no Linux
capabilities and no network, plus 31 deployment tests on Linux. Windows passed
21 deployment tests and skipped 10 POSIX cases. Python 3.7 compiled the sealed
assets and passed the wrong-host zero-query guard. The production category-only
PNG and ordinary Sandbox visibility checks passed. The Gateway status supplied
by the persistent inspection showed generation 414 with all 70 replicas
updated, ready and available, and no terminating replicas. That status is not
a per-Pod source audit or a conversation-link business test. Real-conversation
PNG acceptance and complete file delivery remain pending. No schedule or Work
message has been created by this repair.
