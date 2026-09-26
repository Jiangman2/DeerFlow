INTERNAL BUILD INPUTS ONLY - NOT A PRODUCTION HOTFIX
Validate with: python3 -B verify_candidate.py . (the package directory must remain immutable).
No rollout/rollback scripts or image archives are included. No production writes are authorized by this bundle.
Use verify_oci_base.py to check each exported local OCI layout against its delta JSON before binding base contexts.
A runtime image ID is not assumed to be a registry manifest digest. All selected local layer blobs must match their descriptors.
Build in isolated BuildKit with data under /opt, CPU/I/O/memory/concurrency bounds, and no Gateway colocation.
Every RUN disables networking. No pip/apt/pnpm installation is performed; missing base dependencies fail the build.
Named base contexts MUST be supplied by a verified offline builder; do not permit fallback image pulls.
Do not run apply_image_delta inside a live production container. A failed disposable build layer must be discarded.
Source checks/imports/builds are not business acceptance. Do not restore old images/data over new publications, revocations or employee edits.
