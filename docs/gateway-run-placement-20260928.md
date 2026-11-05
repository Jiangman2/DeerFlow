# Gateway streamed-Run placement (local PVC test stack)

`POST /api/langgraph/threads/{thread_id}/runs/stream` now has an optional
placement path in the `k8s/local-pvc` Nginx config. Before forwarding the POST,
Nginx makes an internal, bodyless subrequest to `gateway-run-dispatcher:8004`.
The dispatcher lists Ready Gateway Pods and counts their unexpired
`pending`/`running` Run leases in PostgreSQL, then returns the least-loaded Pod
IP in a response header. Nginx sends the original authenticated POST and SSE
directly to that Pod. The dispatcher never receives request bodies, cookies, or
SSE bytes. It does not own or execute Runs.

This is approximate, not a cluster-wide atomic queue: each of the two
dispatcher replicas holds short five-second reservations so a burst does not
repeatedly pick the same Pod before its Run rows appear. Concurrent selectors
can still briefly choose the same target. The existing Gateway Run conflict,
authorization, and lease rules remain authoritative. If every Pod is busy,
the selector still picks the least-loaded one; it does not reject or silently
drop a request. Future work may add a bounded shared queue if saturation
persists. `DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS` still limits model streams,
not Run ownership. Non-stream Run creation, scheduled tasks, and IM calls retain
their existing routing and are outside this first load-test cutover.

## Safe test-cluster rollout

Do this after the current load test's Runs have finished. Build and import a
new Gateway image containing `backend/app/gateway/run_placement.py`. Edit the
image placeholder in `k8s/local-pvc/gateway-run-dispatcher.yaml` with the
tested image digest or a unique release tag; it does not need to replace the
images of the 22 existing Gateway Pods.
Apply **only** this standalone manifest first, not the whole local-pvc
kustomization: the live cluster has manually tuned resources and ConfigMaps
that a broad apply could revert. The dispatcher is deliberately absent from
the default kustomization and the checked-in Nginx config is deliberately
unchanged; this feature stays off until the two-location cutover below.

Before building the image, run the focused checks on the Linux host:

```bash
cd /opt/DeerFlow/backend
uv run --no-sync ruff format --check app/gateway/run_placement.py tests/test_run_placement.py
uv run --no-sync ruff check app/gateway/run_placement.py tests/test_run_placement.py
uv run --no-sync pytest tests/test_run_placement.py -q --tb=short
```

```bash
kubectl --kubeconfig "$kcfg" -n deer-flow apply -f k8s/local-pvc/gateway-run-dispatcher.yaml
kubectl --kubeconfig "$kcfg" -n deer-flow rollout status deployment/gateway-run-dispatcher --timeout=10m
kubectl --kubeconfig "$kcfg" -n deer-flow exec deploy/nginx -c nginx -- \
  wget -qO- http://gateway-run-dispatcher:8004/ready
```

The last command must report `ready_gateway_pods` equal to the intended number
of ready Gateway Pods. Confirm Nginx has `--with-http_auth_request_module` in
`nginx -V` before cutover. Then merge **only** the two Nginx `location`
blocks from `k8s/local-pvc/gateway-run-placement-nginx.inc` into the live
`deer-flow-nginx-config`; preserve the live `worker_processes 2`,
`worker_connections 2048`, `/nginx-health`, all existing routes, and the
Nginx 2 CPU / 1 GiB resource settings. The ConfigMap is mounted using
`subPath`, so restart Nginx after editing. Keep a recoverable snapshot:

```bash
kubectl --kubeconfig "$kcfg" -n deer-flow get configmap deer-flow-nginx-config -o yaml \
  > /home/cnooc/deer-flow-nginx-before-run-placement.yaml
kubectl --kubeconfig "$kcfg" -n deer-flow edit configmap deer-flow-nginx-config
kubectl --kubeconfig "$kcfg" -n deer-flow rollout restart deployment/nginx
kubectl --kubeconfig "$kcfg" -n deer-flow rollout status deployment/nginx --timeout=5m
kubectl --kubeconfig "$kcfg" -n deer-flow exec deploy/nginx -c nginx -- nginx -t -c /tmp/nginx.conf
```

Confirm the edited ConfigMap contains the two locations exactly once before
the restart. A five- or ten-Run smoke test should precede another 800-thread
test.

The selector has a ClusterIP-only Service, a namespace-scoped `list pods` RBAC
grant, a two-connection PostgreSQL pool per replica, and no user-auth bypass.
`/select` returns only a validated Ready Pod address; Nginx keeps the selector
subrequest internal and does not forward browser headers or bodies to it.
Selector failures return 503 to the internal subrequest (Nginx can surface
this as HTTP 500), which prevents the original POST from being sent;
investigate readiness before cutover rather than silently selecting a random
Pod. A selected Pod can become unready during the five-second readiness-cache
window, so watch for rare 502s during Gateway rollouts and avoid rolling the
Gateway while the load test is running.

For rollback, restore the saved ConfigMap with `kubectl apply -f
/home/cnooc/deer-flow-nginx-before-run-placement.yaml` and restart only Nginx.
Existing Gateway Pods and their in-flight Runs need not be changed. The
dispatcher Deployment can remain idle or be scaled to zero later.

## Acceptance signals

- `GET /ready` sees every expected Ready Gateway Pod; Nginx `nginx -t` passes.
- Dispatcher `RUN_PLACEMENT event=selected` logs contain the chosen Pod and
  pre-selection active Run count, never prompts or credentials.
- During a controlled load test, compare per-Pod **active** Run and model-stream
  peaks, not just final `total_runs`. A narrower spread and lower high-Pod
  `downstream_ms_per_chunk` are the desired signals.
- Record Run success/error separately from model-call errors. A provider
  `RemoteProtocolError` may recover within one Run; placement is intended to
  reduce local skew, not to guarantee that an upstream model stream never
  disconnects.
