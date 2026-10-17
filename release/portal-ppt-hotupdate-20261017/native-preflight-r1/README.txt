Prepared native preflight r1 -- read-only, no runtime fixture or deployment

The single collector is intended for datax-01. Root reviews and delivers the
frozen file before the operator runs it; this directory alone is not execution.

Proposed operator command (ordinary cnooc shell owns the redirection):
(umask 077; set -C; sudo python3 -I -B -u /home/cnooc/collect-portal-ppt-native-preflight-20261017-r1.py --collect > /home/cnooc/portal-ppt-native-preflight-20261017-r1.REPORT.json)

Only sudo Python accesses the admin kubeconfig. The outer shell creates the file
with mode 0600 and noclobber; an existing report is preserved. No root handoff,
copy/chown, wrapper, or root-owned report directory is necessary. Do not redirect
stderr into REPORT.json: stderr is safe progress and the final byte/SHA256 summary.
The final summary hash covers the exact UTF-8 JSON bytes INCLUDING its final LF.

Scope: fixed datax-02 node, four deer-flow Deployments, StatefulSet postgres and
its owner-UID-filtered Pods, nodes directly observed for those PostgreSQL Pods,
and a resource-only go-template listing of Pods scheduled on datax-02. Unknown
resource semantics become an explicit gap; no capacity or rollout pass is issued.
No CNI objects, Secret/ConfigMap contents, DB connections, source scans, exec,
SSH, image cache inspection/pull/import, workload mutation or model calls.

The before/after stability comparison covers the listed workload objects,
PostgreSQL Pods and target Node. It does not recheck 70 Gateway Pod identities.
The report always has ready_for_native_execution=false, ready_for_build=false,
ready_for_rollout=false. CNI enforcement and exact native image cache binding
remain later execution gates. A complete read is not approval to run fixtures.

Offline qualification uses Python stdlib fixtures and the already installed
kubectl typed create-configmap client generator. Full Go-template compilation,
missing-field rendering and actual resource-map rendering are covered. The
ordinary-container/Always-init/max-init/overhead accounting is tested in Python.
There is no claim of a real nonempty PodList being rendered offline. The attempted
kubectl run client generator tried localhost discovery and was abandoned; it is
not part of the delivered collector or final tests and no production API was used.
