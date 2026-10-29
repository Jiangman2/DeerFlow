# Optional V4.1 enablement, 2026-10-29

**Superseded execution workflow:** the operator subsequently requested separate
configuration preparation and a later Gateway rollout. Use `SPLIT-ENABLEMENT.md`.
The original one-step executable and the command below are retained only as
historical implementation/qualification evidence, not the current run instruction.

The operator corrected the credential binding: V4.1 uses the existing GLM
credential (`GLM_5_3_FLASH_API_KEY`), rather than the V4 credential. The r2
production verification returned HTTP 200 for all seven fixed synthetic requests.
The immutable operator report is pinned in `provider-acceptance-field-summary.json`.
The summary is not a byte-for-byte copy of the server report. The activation tool
requires the actual report's SHA256, length, and successful case semantics.

The endpoint and installed adapter passed both the native document request and
the adapter tests. Earlier HTTP 402 responses used the different V4 credential.
No new provider requests are made by the enablement tool.

## Scope

`enable_host.py` and `enable_child.py` are packaged by `package_enable.py` into
`activate-portal-v41-glm-20261029-r1.py`. The packaged tool is the executable
delivery; the templates refuse to run unsealed. Run only on datax-01 as root:

```bash
sudo python3 -I -B -u /home/cnooc/activate-portal-v41-glm-20261029-r1.py --enable-v41-optional-once
```

The exact starting baseline is Gateway 389, Frontend 231, Runner 55 and serving
Nginx generation 2. Only Gateway's config-volume ConfigMap reference changes.
An immutable new ConfigMap contains the old payload plus one appended model.
Every existing config setting and model retains its value and order. The first
model, `deepseek-v4-flash`, remains the default. The old ConfigMap is preserved,
including for Runner's use. Images, Secret data, frontend, Nginx, Runner, and the
paused Work update are outside this mutation. No database migration is requested.
Gateway's ordinary application startup still runs normally on the new Pods.

The user authorized switching with running tasks. This remains a Gateway rolling
update and may interrupt tasks; it is not a zero-interruption configuration reload.
The existing deployment strategy, replica count and scheduling are preserved.

Before mutation, the tool checks the real mounted config, adapter and CA hashes,
constructs the configured model in both thinking modes without making requests,
and requires a server-side dry run. A resourceVersion/UID/generation/full-spec
compare-and-swap prevents overwriting concurrent deployment changes.

The one-use journal is `/var/lib/portal-v41-glm-activation-20261029-r1`.
Snapshots and candidate configuration are root-only files. Reports export only
metadata; credentials and configuration bodies never appear in public output.
The tool submits at most one deployment patch. If its reply is lost, it reads
back the expected state instead of reposting. No automatic rollback occurs.

Acceptance requires all 70 Gateway replicas, each mounted candidate-config hash,
the exact model adapter, the GLM binding, successful local model construction,
local HTTP health, and authentication still denying anonymous `/api/models`
requests. Service endpoints must match the accepted Pods; protected deployments,
Services and original ConfigMap must retain their prior bindings. This checks
runtime configuration and readiness, not a logged-in browser model selection or
new business conversation. Those remain operator acceptance after rollout.

## Failure / rollback

If any check fails, preserve the JSON and journal. Do not rerun, remove the
one-use directory, rerun historical tools, or use an unguarded rollout undo.
`patch_may_have_applied=true` requires inspection even when final acceptance
failed; an unconfirmed `v41_enabled` is reported as null, not false.

The root-only `before.private.json`, `planned-spec.private.json` and old
ConfigMap preserve rollback inputs. A rollback must first bind the current
Gateway state and then restore only its previous config-volume reference through
a fresh compare-and-swap. It requires another Gateway rollout; no database or
Secret rollback is needed. V4.1-selected conversations would require selecting an
available model again. Never roll back unrelated concurrent changes.

The current production source lock remains historical and unchanged. A successful
activation receipt must be archived as new configuration provenance; preparing
this tool does not mean V4.1 is already enabled.
