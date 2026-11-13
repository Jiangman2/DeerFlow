# Delivery Skill install sources and frozen package

This new source directory prepares a Skill-only update. `operator.py` is the
unsealed template and refuses before any Kubernetes command. The reviewed
107-file 2.1.0 candidate is frozen by manifest SHA
`8ddbf43d4e08f08e485b85f96814f7680d96b976989b61340c230603b37d3bef`.
Run `python seal.py` to reproduce the deterministic ZIP and sealed entry under
`release/energy-daily-delivery-skill-20261112-r1`; an existing different release
file is rejected. No production operation is performed by sealing or tests.

The prior accepted server receipt is fixed to
`/var/lib/portal-energy-public-runtime-repair-20261112-r1/REPORT.json`, 6038 bytes,
SHA256 `8a36ab323f83d7bc1fba16ef7eac06faa07520fa109efb86ffe087af4d733ce0`.
The only accepted old Skill tree is
`f9865f7b1fd1745c471bb808f081bf3e86f2170c0c9c22df20e776c0a56ffcf9`.
The public browser runtime remains at
`/mnt/skills/public/.energy-browser-runtime-20261112-r1/linux-arm64`, with manifest
`cef35e8edb510631197d5d4874e2e9d6fb2c9ef93a7be8393f38fa9446ea0598`
and source image `sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8`.
This batch verifies and reuses that dependency; it has no browser installation
or runtime-copy routine.

The staged Skill and its private backup remain outside public discovery.
Qualification uses an owned, fixed-image Pod: the entire real `public` category
is mounted read-only at `/mnt/skills/public`, with only the private staged
candidate overlaid read-only at `/mnt/skills/public/energy-market-daily`.
The existing public sibling browser dependency is therefore resolved exactly
as in ordinary sandboxes. No additional browser mount aliases are introduced.
UID/GID 1000 renders the known fixed PNG in its own temporary directory, then
tests compose-only final text with complete files, a missing artifact, a missing
publication projection and missing market values. The probe blocks sockets,
external processes and database connections. A synthetic publication projection
does not prove a real host publication or a Work/webhook send.

Only the qualified Skill is switched through private backup plus two short
renames. Historical reports, existing sandboxes, other Skills, credentials,
Provisioner and Gateway are not changed. Temporary Pod/ConfigMap cleanup is
operation- and UID-bound. Ordinary sandbox post-publication checks only observe
visibility; they never render PNGs in another user's sandbox.

Run the Skill update to completion **before** the separately prepared Gateway
415 budget rollout. The skeleton pins Gateway 414 and rejects protected
Deployment drift before publication. It must not be run concurrently with the
budget change. There is no automatic retry or rollback.

The sealer fills the version/count/hash markers and embeds only the frozen
candidate, fixed fixtures and hashed compose probe. Installed file count is
108, including the separately added production browser pointer. Review the
exact sealed entry before handing it to the operator. Its own real
ARM64/UID1000 render and compose qualification must pass before publication;
local API checks do not substitute for that gate or real business acceptance.
