# PPT Live Preview runtime-profile repair R4 — build-only

This package changes only `app/gateway/routers/artifacts.py` in the production
Gateway and dedicated PPT Runner. It derives PPT preview routing from the
newest owner-scoped, durable `ppt-master-runner` run, not client-writable thread
metadata. The bounded 5050–5099 and 6060–6109 port windows, thread authorization,
Runner forwarding, and Sandbox authentication remain in place. Existing PPT
threads with a missing profile (including the reported 6060 case) are covered
without a database migration or editing user data.

This is a **build-only** offline package. It does not import images, patch a
Deployment, or prove live browser acceptance. Its bases are the deployed
context-fix Gateway and R3 receipt-recovery Runner archives, not the older R2
images. The guarded builder verifies package checksums, exact live source
hashes, OCI bases, deployment images, resource budgets, and candidate images.
If any baseline differs, stop; do not force the check.

On `datax-01`, verify the copied archive and extract it under a new root-owned
directory in `/opt/deerflow-hotfixes`. Then run:

```bash
sudo bash run-datax01.sh --check-only --output /opt/deerflow-build-artifacts/ppt-preview-profile-r4/build1
```

Only after `ppt_runtime_build_setup=PASS`, run the same script with
`--build-only-ack YES` and that **new** output path. The script will still not
roll out. Candidate checksums, five-node distribution/import, runtime canaries,
Gateway/Runner image-pair coordination, rollback-image checks, and browser
acceptance are separate release gates. Do not treat this package as deployment
authorization.

The already-reported thread may have lost its Sandbox after the task ended;
fixing the route does not resurrect an expired preview server. Acceptance must
include a new live PPT task plus an owner-authorized 6060 preview, and ordinary
thread/other-owner negative tests.
