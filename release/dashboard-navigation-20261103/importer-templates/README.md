# Immutable importer inputs

These two byte-identical inputs come from the locally preserved
`release/dashboard-frontend-20261031-r1/importer-templates` preparation. Their
SHA256 pins are enforced by
`deploy/enterprise/package-dashboard-navigation-frontend-20261103.py`.
They are generator inputs, not commands to execute on a production node.

The new generator keeps the original resource reserves, archive integrity,
containerd/CRI visibility and once-only protections. It pins only accepted
historical images (including Frontend 239); the unshipped heatmap r1 candidate
is deliberately absent from the required-old-image list. Generated candidates
use fresh paths and require a separate production rollout verification.
