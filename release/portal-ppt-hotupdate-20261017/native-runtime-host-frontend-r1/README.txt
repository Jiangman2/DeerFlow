Host / Frontend stage helper for the unified native runtime controller

This directory contains pure rendering and receipt validation only. It has no
Kubernetes client, lifecycle entrypoint, SSH, candidate process execution, model
request or production mutation. The controller embeds host_frontend_stage.py as
one self-contained standard-library Python source module.

Source boundary
--------------
The exact original native-host-frontend-r1 ConfigMap payloads, Pod templates and
binding template from the prepared merge tree are bundled in ASSETS.json. The
original artifact manifest is checked before binding. Host adapter/probe,
Frontend adapter, synthetic fixture and fourteen-check HTTP harness bytes remain
unchanged. The accepted final preload report binds Host runtime path inputs and
the exact Host / Frontend manifest and config digests already preloaded on datax-02.

Interface
---------
render(owner, namespace, host_runtime_inputs) returns a dictionary with stages,
binding, requirements and source_assets_sha256. The namespace must be exactly
portal-ppt-<owner>, where owner is 32 lowercase hexadecimal characters. Each stage
contains objects, pod_name, images, terminal_container, deadline_seconds and
pending_seconds. All objects carry portal-ppt-network-owner=<owner>.

The Host and Frontend use separate binding ConfigMaps for serial ownership and
cleanup. Frontend images includes both the synthetic-projection init container
and frontend-probe main container. terminal_container selects the actual adapter
receipt; init-container logs are not interpreted as an adapter result.

validate_stage_objects(stage, owner, namespace) checks exact rendered requests.
It does not validate admission; the controller separately verifies the actual
admitted spec and UID of every created object.

validate_terminal(role, raw_bytes, observed, namespace) accepts bounded raw JSON
from the actual terminal container. observed has exactly: pod_uid, node_name,
node_uid, image_id, image_reference, pod_phase, exit_code, restart_count and
container_id. Host requires all thirteen expected sources and the three original
runtime checks. Frontend requires all fourteen exact HTTP check names plus both
layout and HTTP child exits. False native/rollout flags in the original adapters
are retained: only the unified controller can combine those receipts with its
independent live node, image, admitted-spec and CNI network proof.

Controller prerequisites
------------------------
Use the same unique namespace, owner label and verified deny-all policy as the
network proof. Require proof completion before creating either candidate Pod.
Validate all init and main container image identities, terminal exits and zero
restarts, enforce bounded pending/runtime deadlines, and clean only exact UIDs
owned by this run. No PVC, application Secret or service-account token is mounted.
Host runs local browser and Codex --version checks; Frontend uses synthetic local
HTTP fixtures. These steps do not submit a model request or certify rollout.

Qualification
-------------
QUALIFICATION.json records Python 3.7 syntax acceptance, exact asset and source
pins, the accepted 24,125-byte preload report, its Host input projection, and
seven local renderer/receipt fixtures. This is local helper qualification only;
the native ARM64 run must still happen on the operator's cluster.
