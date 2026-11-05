# Independent Gateway Dispatcher source

This is the owner-cancellation Dispatcher source accepted in the October 4
release, restored from the exact commit recorded in `manifest.json`.
`docs/production-gateway-reconciliation-20261004.md` records its original
acceptance; October 22 deployment metadata still reports the owner-cancel image.
No new live source fingerprint or container rebuild is claimed here.

Gateway's sampled image contains an older **unused** `app/gateway/run_placement.py`.
It cannot be used as the source for the independently deployed Dispatcher.
The owner-routing tests load this complete executable module, verify its hash and
version, then exercise all existing SQL binding, owner disappearance, header,
readiness and non-reassignment assertions. They do not replace its behavior with
stubs; only Kubernetes/database transport is synthetic.

For a Dispatcher release, overlay this file at
`/app/backend/app/gateway/run_placement.py` in the separately qualified Dispatcher
image before running `uvicorn app.gateway.run_placement:app` on port 8004.
Do not build that service from the dormant Gateway copy or overwrite the ordinary
Gateway source with this file. Existing deployment manifests retain their explicit
tested-image placeholder; no service is changed by this source recovery.
