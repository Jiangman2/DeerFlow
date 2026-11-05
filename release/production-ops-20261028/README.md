# Completed operations archive — October 28, 2026

This supplements the Gateway 389 / Frontend 231 / Runner 55 source baseline at
`3587193b8f6c009cae34a6d0339fbd97e831a873`. It archives completed operations and
the installed **ecs-zdz-pro-0015-only** auth.log maintenance program. It does not
deploy, install, rotate, recover, relocate or restart anything. Datax-01 policy
installation was declined. Work and further WeKnora provenance work are excluded.

## Accepted scope

| Area | Evidence and boundary |
| --- | --- |
| 0015 auth.log | Initial rotation retained the complete old log on the data disk. The installed program checks hourly at minute 17 and rotates at 100 MiB; original permissions and security attributes are preserved. Installation and dry-run checks passed. No first scheduled-run receipt has been received. |
| Dispatcher and SkillHub | Original image references were restored after verifying retained image contents. Existing Pods recovered without deployment patches or Pod deletion. The evidence directory contains explicitly labeled projections of operator output, not copies of full server reports. |
| Alloy | The verified original offline image was imported once. The existing Pod became ready; the DaemonSet and protected deployments were preserved. The image archive itself is not committed. |
| Datax-01 and 0015 archives | Completed transfers retained verified data-disk copies and original-path symlinks. These copies remain required; this archive does not authorize deleting them. Original data files and log contents are not committed. |
| Frontend 230 | Historical preload, failed preflight and accepted activation records plus its source inventory are retained. Version 231 supersedes 230 and already includes its cumulative runtime changes. The old source inventory is historical evidence, not an instruction to restore old sources. |

`historical/` preserves the selected original scripts, tests, records and notes
byte for byte. Earlier notes may say pending: use the later successful field
receipt for that operation, without rewriting the older record. Original tool
paths were `deploy/enterprise/`. `delivered/` preserves the exact policy installer
sent to the operator, including its embedded transfer implementation. Its SHA256
is `4b42db64116b0436e70abc9ccf8eb01888dfd957e863e9453927fceb4e5730f3`.
The logrotate rule and cron generator are in that source; the installed cron's
Python path is resolved on the host and is not guessed by this archive.

`MANIFEST.json` records each original path, byte length and digest. The verifier
pins the manifest, reconstructs the delivered installer from its two sources,
and checks the installation's binding to the original transfer receipt. Operator
summaries preserve the full report's server path/digest without claiming that a
summary hashes to the full report. The remaining WeKnora base-image provenance
limitation stays unchanged in the existing reconciliation documents.

## Verification

From the repository root, run only the offline qualification commands:

```sh
python deploy/enterprise/verify-production-ops-archive-20261029.py --ref HEAD
python -B -m unittest discover -s deploy/enterprise -p test_production_ops_archive_20261029.py
```

The separate CI job also exercises the archived unit tests on a disposable Linux
runner with native logrotate, temporary files and mocked service/container
transports. Recovery entry functions are exercised only with synthetic inputs
and mocked transports; no host or cluster maintenance command is dispatched.
The optional full Alloy image transport test requires the retained 141 MB package
and is skipped if absent; its historical executed qualification is recorded.

Do not run historical deployment or maintenance commands from this directory.
A future operation needs a current baseline and a newly reviewed tool. Source
alignment does not establish fresh live-cluster health or complete byte-identical
image rebuilds.
