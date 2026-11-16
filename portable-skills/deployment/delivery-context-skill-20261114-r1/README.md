# Delivery context Skill 2.1.2 — unsealed publication template

This directory derives from the accepted 2.1.1 Skill-only installer. Historical
sources and receipts remain unchanged. No executable installation package is
generated here. `operator.py`, `publisher.py` and `verify_child.py` deliberately
retain unsealed fields; the Gateway context receipt also has unbound identity
fields. Both contracts must pass source review before sealing.

The current Skill receipt is
`/var/lib/portal-energy-freight-links-install-20261114-r1/REPORT.json`, 6,929 bytes,
SHA256 `32eb29be3a4fffe6f13702a805d952e08db5b60e08e717b74fd5a9656ee945ea`,
operation `daeceae4165e4e1d87050bd97e6bf933`, 108 installed files, tree SHA256
`9853df2b5db3966266ee4481ca32e972752f186ced1a41ac03205b19bec0c04b`.
Its saved status is `ENERGY_DELIVERY_SKILL_INSTALLED_VISIBILITY_PENDING`;
the subsequent read-only visibility result did not rewrite this historical
receipt. The template matches that exact status and independently requalifies
the current shared mount, instead of requiring the archived receipt to change.

## Required order and receipt boundary

First deploy and verify Gateway's per-command conversation context. Then bind
the root-owned Gateway acceptance receipt at
`/var/lib/portal-task-conversation-context-postcheck-20261114-r2/RUNTIME-ACCEPTANCE.json`.
The template checks its reviewed schema/status, exact bound bytes/hash/operation,
accepted parent/candidate source hashes, completed 70-Pod verification, all 14
source files, service backends, preserved budget 10000, and current Gateway
UID/generation/spec. Before any once-claim or staging, an unbound, incomplete,
changed or stale Gateway receipt stops the installer. The acceptance is re-read
before switching; all protected deployments must remain unchanged.

The final exact receipt binding is intentionally pending root review and the
Gateway deployment result. An isolated synthetic context test in that receipt
qualifies extracted source functions and a synthetic transport prefix, not
loaded application modules or a real user's business transaction.
The r2 postcheck accepts only the proved `defaultMode: 420` on the newly added
ConfigMap volume. The installer binds the original failed operation and plan,
the actual generation-417 spec hash, and the single-difference evidence; no
activation or patch is replayed. It binds the actual saved acceptance bytes.
Only the first successful terminal JSON may be reconstructed as described below;
the output of a later repeated check is not the original saved receipt.

## Skill-only qualification and publication

Reuse the existing public sibling ARM64 browser runtime, manifest
`cef35e8edb510631197d5d4874e2e9d6fb2c9ef93a7be8393f38fa9446ea0598`;
do not copy/install browsers, read credentials or alter existing Sandboxes.
Use the fixed cached image with `imagePullPolicy: Never`, UID/GID 1000 and the
real `public` category mount. Run 55 offline regressions against the mounted
candidate. Then render the existing complete sample and the dated freight
fallback sample with the real browser. The fixed sample retains PNG SHA256
`bedd5af036641c695510cca936b1e530ed7600238a8892fe88b5d7d5953bdd9b`.

The qualification child clears its environment and injects a visibly synthetic
`example.invalid` conversation URL. This checks Skill composition only and
explicitly reports `real_host_context_injection_proven=false`; it cannot replace
the separate Gateway receipt. It does not call `present_files`, send messages,
query real prices or read credentials. The complete TXT contains one link at
build time; accept requests one real file publication followed by reading that
same TXT. No model-written publication JSON or normal `final-text` action is
required. Missing current context cannot generate a send-ready final draft.

After source review, sealing must include fixtures `test_energy_market_daily.py`,
`test_energy_single_delivery_20261114.py`, `fixtures/approved.json` and
`fixtures/original_renderer.py`, `fixtures/dxy-source.txt` and
`fixtures/cny-source.txt`, and bind their hashes in `verify_child.FIXTURES`.
`REVIEWED-SOURCE.json` locks the reviewed candidate code. The copied historical
manifest and `CANDIDATE.md` are excluded from the published package. The sealer
generates an independent 2.1.2 manifest from those exact reviewed bytes. Core market,
research, calculation and PNG code must remain byte-identical to 2.1.1.

Only the qualified Skill is switched after a private backup. Cleanup is limited
to owned temporary Pod/ConfigMap UIDs. A ready ordinary Sandbox is inspected
read-only for post-publication visibility; no browser is launched inside it.
No Gateway, Provisioner, credential, schedule, history or other Skill mutation
is included. Final real task, file-publication and Work delivery acceptance
remains pending after installation.

## Seal only after the real Gateway acceptance arrives

Download the exact completed `RUNTIME-ACCEPTANCE.json` without reformatting it.
Alternatively, save the first successful terminal JSON: the sealer removes only
`runtime_acceptance_receipt`, reconstructs the canonical saved bytes, and accepts
them only when the metadata's fixed path, byte count and SHA256 match exactly,
`existing_acceptance_reused=false`, and
`stdout_without_receipt_metadata_matches_saved=true`.
A later check with different timing fields fails this reconstruction and must
use the original saved file; there is no permissive fallback.
After the fixed receipt contract has been reviewed against the actual Gateway
verification revision, run locally:

```text
python portable-skills/deployment/delivery-context-skill-20261114-r1/seal.py --gateway-receipt <downloaded-acceptance.json>
```

Missing, incomplete, changed-source or partial-fleet receipts are rejected
before any release file is written. The generated installer binds the server's
fixed receipt path, byte count, SHA256 and operation; it rereads the exact
root-owned receipt and checks the live Gateway identity before claiming once.
The independent manifest, deterministic ZIP and sealed installer are written
to `release/energy-daily-delivery-context-20261114-r1`. Existing different release
files are rejected. Sealing performs no network or production commands.

Unit tests create synthetic receipts and sealed assets only under temporary
directories that are cleaned afterwards. They never write the production release
directory or claim a synthetic receipt is production acceptance.
