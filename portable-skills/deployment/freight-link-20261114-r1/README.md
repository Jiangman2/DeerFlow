# Freight and conversation-link Skill update

This directory prepares the 2.1.1-portable Skill-only update. Its sources derive
from the accepted 2.1.0 installer; historical sources, releases and receipts stay
unchanged. `operator.py` is an unsealed template and refuses before Kubernetes
commands. Candidate version, manifest digest and exact file counts must be
reviewed and frozen in `seal.py` before generating a runnable package.

The accepted prior receipt is
`/var/lib/portal-energy-delivery-skill-install-20261112-r1/REPORT.json`, 7,038 bytes,
SHA256 `780034c2c0778a06bd6bd2957bb32dcb4e15898997cf20eae451e33c73356e02`,
operation `722481d5ab5246cf8d959cb7c3b213e0`. The accepted old Skill has 108 files
and tree SHA256
`8ac3ca5df517d5ed269593d7c9e1216cfc758bf76b3367a40e4d05f372df7ccc`.

The existing browser runtime is verified and reused at
`/mnt/skills/public/.energy-browser-runtime-20261112-r1/linux-arm64`, manifest
SHA256 `cef35e8edb510631197d5d4874e2e9d6fb2c9ef93a7be8393f38fa9446ea0598`,
from image ID
`sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8`.
There is no browser installation/copy routine and no credential access.

The installer reads current Gateway, Frontend and Provisioner identities and
spec hashes, then requires the same snapshot before and after publication. It
does not pin an obsolete Gateway generation or require a separate Gateway
rollout. The mutation allowlist permits only its own temporary Pod/ConfigMap and
this Skill's files. The fixed cache-only ordinary image is used with
`imagePullPolicy: Never`; no existing business Sandbox is restarted or modified.

Staging and the private backup remain outside public discovery. Qualification
uses the real public category mounted read-only at `/mnt/skills/public`, with
only the staged Skill overlaid read-only at its normal child path. UID/GID 1000
resolves the public sibling browser dependency and renders the complete fixed
PNG. The unchanged fixed sample must retain its accepted SHA256
`bedd5af036641c695510cca936b1e530ed7600238a8892fe88b5d7d5953bdd9b`.
The compose probe verifies final text and the newly requested freight/link
behaviors without network or messages. Synthetic trusted-context inputs do not
prove a real platform publication or Work/webhook send.
The same sealed probe also renders a second synthetic report with missing target
freight quotes and dated historical fallbacks. Its actual dates must be present
in HTML, JSON and text. This second real PNG does not reuse the fixed sample hash;
its dimensions, byte count and reported SHA256 are checked separately. Both
renders run under UID 1000 and clean their private executable copies.

Only the qualified candidate is switched using a private backup and two short
renames. Historical reports, other Skills, credentials and existing workloads
are preserved. Cleanup checks the owned operation and Pod/ConfigMap UID.
After publication, one current Ready ordinary Sandbox with the accepted image
is selected dynamically for a read-only public-mount visibility check. It never
renders inside another user's Sandbox. A missing/recycled ordinary Sandbox is
reported as visibility pending, not as successful business acceptance.

`seal.py` builds a deterministic ZIP and sealed entry under
`release/energy-daily-freight-links-20261114-r1`; existing differing release
files are rejected. Local tests and sealing never execute the production entry.
After freezing, run the template API, compose and sealed-package tests, including
the Python 3.7 wrong-host and sealed-asset checks. Field ARM64 rendering is still
required inside the installer before publication. No automatic retry or rollback
is performed, and no schedule or Work message is created by this installer.
