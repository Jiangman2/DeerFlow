# WeKnora UI v0.8.0 enterprise R5 source

This directory contains the complete upstream `frontend/` tree from WeKnora
commit `1edcd54b43606d9079bb36650efe3f68707a79ea` and the exact R5
enterprise UI overlay used to build the 2026-09-13 ARM64 base image. The
server-side R5 package SHA-256 was
`aede47bf923be5be46309ffb14b95883c2fddcb93d5670d3b9726661c5bef275`;
its embedded upstream archive SHA-256 was
`b70c7a3c69fe9a7e3e7c5ef9ecd50f94f870865decbadb865b9b66828b38143e`.
All 18 R5 package entries matched `PACKAGE.SHA256` before importing selected
build inputs here. `FRONTEND.SHA256` records every vendored frontend file.

The R8 production source-stage snapshot matched 611 of these 612 frontend
files byte for byte. Its source collection omitted
`src/components/credentials/CredentialResource.vue`; the R5 source archive
contains it. The 14 R5 upstream UI lock entries all match the R8 source-stage
copy. The R5 enterprise bootstrap JavaScript and CSS also match the currently
deployed 2026-09-18 UI image byte for byte.

On an approved ARM64 Linux builder with the pinned BuildKit frontend, Node and
Nginx images already available, set `ENTERPRISE_CA_FILE` and optionally
`BUILDKIT_ADDR`, then run `build_oci.sh /absolute/path/to/new.oci.tar`.
This writes a local candidate OCI archive only. The script follows the R5
BuildKit options, restores the one upstream source file's official LF bytes in
the temporary context, and refuses source-hash drift. The builder also needs
the operator-owned egress proxy values used for the original npm install.
It does not import or deploy
the image. The current 2026-09-18 production UI image additionally applies
the `sidebarlabels1` layer; building R5 alone does not produce that final
image. Verify any candidate against the production lock before claiming image
identity.

The 2026-09-18 `sidebarlabels1` overlay is already tracked under
`deploy/hotfixes/deerflow-skill-authoring-defaults-20260919-r11/`. A separate
server archive of its six build inputs had SHA-256
`081fe2402f31d62545594cf2067fd68bbe358a140855676807726af0d2242561`;
each archived file matched the tracked file byte for byte. On the same ARM64
builder, `build_production_candidate.sh /absolute/base.oci.tar
/absolute/final.oci.tar` runs both builds and verifies the final OCI manifest,
config and layers against `PRODUCTION-IMAGE-LOCK.json`. The candidate remains
local even if its digest differs. A passing comparison has not yet been
recorded.

The upstream license and third-party notices are in `LICENSE.upstream`.
