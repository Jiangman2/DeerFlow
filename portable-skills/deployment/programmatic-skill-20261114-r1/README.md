# Programmatic delivery Skill 2.1.3 — frozen publication source

This directory contains a new Skill-only installer generator. It does not alter
the published 2.1.2 candidate, historical installers or production services.
The root reviewer approved the final freeze. `REVIEWED-SOURCE.json` and
`CORE-SOURCE-PRESERVATION.json` now bind that review, and the sealed release is
under `release/energy-daily-programmatic-20261114-r1`. The reusable template
files deliberately retain unsealed placeholders; only the generated installer
is executable. No production installation has been performed by local sealing.
The four sealed assets and wrong-host guard passed actual Python 3.7.17 in a
read-only, network-disabled container. ARM64/public-mount rendering remains an
on-server prepublication gate in the installer.

The exact installed baseline is 2.1.2, operation
`1ba3b07ffe08419d878e1f3460351c9d`, server receipt
`/var/lib/portal-energy-delivery-context-skill-install-20261114-r1/REPORT.json`,
8,446 bytes, SHA256
`0da0874fec0386d3d0fd5bb33afa614fff97c3b059452ef16465abe981bfba88`,
status `ENERGY_DELIVERY_SKILL_INSTALLED`, 108 files, tree
`915a4c765fc81e20b054bc77aa08ce9e919599d354a2aee7edc19db6637c1357`.

The existing Gateway 417 receipt is a separate prerequisite, not another update:
`/var/lib/portal-task-conversation-context-postcheck-20261114-r2/RUNTIME-ACCEPTANCE.json`,
4,064 bytes, SHA256
`e139a6a2d05b797f699aa094a3f92886473731a6c323a5267e7e39cff3e994b7`.
The installer rechecks its source hashes, all-70-Pod acceptance, budget 10000,
and the live Gateway UID/generation/spec before staging and before publication.
Frontend and Provisioner are freshly snapshotted and must stay unchanged.
No Deployment, Secret, schedule, provider, webhook, or existing Sandbox mutation
is permitted. Only this installer's own temporary Pod/ConfigMap may be created
and cleaned by UID.

## Qualification before switching

Reuse the existing browser under the actual `public` subPath. The candidate is
mounted read-only at the ordinary Skill path, and the verifier runs as UID/GID
1000 in the fixed cached ARM64 image with `imagePullPolicy: Never`.

The current candidate qualification set is 27 tests: 19 programmatic build and
legacy acceptance cases, plus 8 explicit news-stage handoff cases. The generator
seals their exact bytes and classes, including immutable 2.1.2 sources used by
legacy-receipt tests. The production verifier relocates only the reviewed path
markers and binds all current code imports to the mounted candidate. A count
change, failed test or skip stops publication.

Two real browser renders follow. The complete fixed sample must retain its
accepted ARM64 PNG SHA256
`bedd5af036641c695510cca936b1e530ed7600238a8892fe88b5d7d5953bdd9b`.
The freight reference sample must retain every actual quote date and reference
label. Its PNG intentionally hides only the global incomplete-data banner;
HTML, JSON and TXT must preserve the incomplete-data facts. The changed freight
PNG is validated and its new hash recorded, rather than compared with the old
banner-containing picture.

Both renders must finish through `build` with
`PROGRAMMATIC_FIXED_TEMPLATE_CHECKS`, `programmatic_checks_passed=true` and
`visual_reviewed=false`. The checks include actual DOM sections and news text,
quote rows, fonts, three viewport overflow checks, and PNG structure. The
qualification never calls `accept --visual-reviewed` or claims an OCR review.

The isolated verifier supplies an explicit synthetic `example.invalid` task URL
and checks the one linked TXT plus real four-file identities. This proves only
Skill composition; `real_host_context_injection_proven=false` and
`real_publication_proven=false` remain explicit. The separate Gateway receipt
qualifies extracted source functions and a synthetic transport prefix, not
loaded application modules or a user's business transaction. No `present_files`
or Work send is performed by this installer.

Only after all gates pass does the publisher make a private backup and replace
this Skill. A ready ordinary Sandbox is checked read-only for public runtime
visibility. Real task completion, artifact publication and Work delivery remain
business acceptance steps after installation.

## Final review and sealing

The final source review records the exact candidate files, test fixtures,
suite/class list and count expected by `seal.py`. The core preservation record
compares five unchanged modules with the **published 2.1.2 adaptation**, not with
every workflow described by the original author's project. The independent
manifest excludes `CANDIDATE.md`, the historical copied manifest, and the
enterprise browser binding; the installer creates the binding separately.

The frozen source can be reproduced with:

```text
python portable-skills/deployment/programmatic-skill-20261114-r1/seal.py --gateway-receipt release/task-conversation-context-postcheck-20261114-r2/OPERATOR-RUNTIME-ACCEPTANCE.json
```

The new output directory is
`release/energy-daily-programmatic-20261114-r1`. Existing different release
files are refused. Local template tests use temporary source-review documents
and return sealed bytes only in memory; they do not publish a release or use
synthetic evidence as production acceptance.
