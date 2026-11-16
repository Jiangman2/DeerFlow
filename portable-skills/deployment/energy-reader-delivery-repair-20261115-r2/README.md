# Serving reader and link-only scheduled Work notification repair

Revision r2 repairs the operator-reported r1 preflight output bound. The failed
r1 operation `5271652f5c234159b7de65f54c638c1f` reported 26 commands, no claim,
no ConfigMap creation and zero patches. The 26th command was Gateway's full
Pod-list query. The original receipt did not record which output stream or its
byte size exceeded the limit. Do not infer those actual sizes from the local
synthetic reproduction.

The Pod query now projects nine identity/readiness/ownership/ConfigMap fields,
omitting annotations, managedFields, environment and full Gateway specs. Only
the single serving Nginx Pod is fetched in full for startup validation. Output
bounds remain enforced; failures report stdout/stderr byte counts and command
kind without raw values. If the original r1 control directory exists, r2 refuses
activation instead of starting a second operation.

All four sealed resources, the exact two notification sources and the 14-file
Gateway manifest are byte-identical to r1. This revision changes only the host
operator query/diagnostics and its separate revision/control directory.

This release is bound to the November 15 operator diagnostic archived in
`release/energy-reader-serving-diagnostic-20261115-r1/`. The operator's final
receipt confirms runtime acceptance at 15:33 Asia/Shanghai on November 15:
serving Nginx generation 3 and Gateway 418, all 70 Pods and actual Service
backends verified. See the sibling sealed release's `OPERATOR-ACCEPTANCE.json`.
Logged-in reading and one real scheduled Work notification remain business
acceptance. The published Skill is unchanged.

Both entry Services select `nginx-security-heai-20261012-r1`, not the ordinary
`nginx` Deployment changed by the previous release. The repair preserves both
Service selectors, the actual serving Deployment UID/spec, existing startup
script and SkillHub authentication. It reads the current template from the
Kubernetes API, never the stale subPath-mounted template. Replaying the seven
existing literal sed substitutions must match the current live file exactly;
candidate replay must also match an independently transformed live file. A
private candidate file must pass the real serving Pod's `nginx -t` before any
cluster mutation. The reader is inserted ahead of existing regex locations.

The two Work modules recognize the generated daily reader footer, validate its
trusted origin, current thread identity and real calendar date, and send exactly
`[点击查看日报网页](<authenticated-reader-url>)`. No title, timestamp, report body,
news links, attachment claim or conversation footer is sent for a daily report.
Missing or conflicting valid reader URLs return `failed` without calling the
sender. Other scheduled notifications retain their existing format. The sender,
delivery identifier, connection binding and outbox schema remain unchanged.
Full web chat replies, TXT and generated artifacts are unaffected.

The operator package first creates two immutable ConfigMap clones with original
contents preserved. It CAS-updates only the existing Nginx ConfigMap reference,
checks convergence, actual Service endpoints, the loaded config and anonymous
IAM redirect for both platform domains. It then CAS-updates only Gateway's
existing six-module overlay reference, replacing precisely the two notification
keys. All 70 ready Gateway Pods must match 14 bound source files, the unchanged
budget configuration (global and scheduler 10000), and 12 synthetic notification
checks. Those checks exercise isolated actual source functions, not already
loaded application modules. Final logged-in reading and one future scheduled
Work message remain business acceptance.

No Skill, credential, frontend, Provisioner, Service selector, schedule or old
report is modified. No provider collection, task replay or Work send is initiated
by the operator tool. Normal application startup and existing background
schedules can still access their normal databases/Redis and send notifications.
The existing rolling strategies are preserved; active Gateway tasks can be
interrupted as previously authorized by the operator.

Copy `repair-energy-reader-work-20261115-r2.py` from the new sealed release to
`/home/cnooc/` on datax-01, then run once:

```bash
sudo python3 -I -B -u /home/cnooc/repair-energy-reader-work-20261115-r2.py --repair-once
```

Keep the final JSON. Do not replay activation after a failure or interruption.
The Kubernetes rollout continues if its watcher is stopped. Check the original
operation without repatching or restarting:

```bash
sudo python3 -I -B -u /home/cnooc/repair-energy-reader-work-20261115-r2.py --verify-and-save-receipt
```

`--verify-readonly` performs the same cluster reads without saving a new local
receipt. Original plans, ConfigMaps, reports and patch journals are retained.

Local validation: 38 regression tests on Windows and Linux/Python 3.10/UID 1000;
real kubectl against a localhost synthetic API projects 70 Pods from 4,635,488
JSON bytes to 8,470 bytes without reading credentials or contacting production;
12 sealed
notification tests under the production no-network/no-write audit; controlled
projected-ConfigMap and escaped-symlink checks; actual Python 3.7 sealed-resource
and wrong-host protection. The unchanged reader had already passed 15 real HTTP
checks for each of three Nginx upstream forms using the cached fixed Nginx image
in isolated containers. These checks do
not prove a real production session or Work notification has succeeded.
