# Enterprise ARM64 deployment

This overlay is the deployment source for the current three-node Kylin ARM64
cluster:

| Node | Kubernetes name | Local storage |
| --- | --- | --- |
| `192.168.111.2` | `datax-01` | DeerFlow home and skills |
| `192.168.111.37` | `datax-02` | DeerFlow PostgreSQL, backups, and WeKnora PostgreSQL |
| `192.168.111.18` | `datax-03` | WeKnora Redis, files, and docreader workspace |

The overlay intentionally exposes only the shared DeerFlow Nginx service at
NodePort `32026`. WeKnora remains behind the same-origin
`/platform/knowledge-bases` route and is addressed by the stable
`weknora-frontend` Service DNS name.

The Frontend deployment must override `WEKNORA_FRONTEND_URL` with the relative
same-origin path `/platform/knowledge-bases`. Do not use a loopback URL or pin a
node address: `127.0.0.1` resolves to the browser user's computer, while the
relative path continues to work when the public IP, DNS name, or TLS endpoint
changes.

The patched WeKnora UI must likewise exchange the DeerFlow session through the
same-origin `/api/weknora/sso-session` endpoint. Both `config.js` and the
`index.html` fallback use that relative URL. Because those files are mounted
from a ConfigMap with `subPath`, restart the `weknora-frontend` Deployment after
changing them; updating the ConfigMap alone does not refresh the mounted files.

## Images

Third-party images are pinned to the ARM64 manifest digests verified on
`datax-01` on 2026-07-21. Gateway, Provisioner, and Sandbox use the hardened
`20260722-enterprise-arm64-rc2` tag. The unchanged Frontend and Skills images
remain on `20260722-enterprise-arm64-rc1`:

- `deer-flow-gateway`
- `deer-flow-frontend`
- `deer-flow-provisioner`
- `deer-flow-skills`
- `deer-flow-aio-sandbox`
- `deer-flow-mineru-adapter`

Custom images must be imported into containerd's `k8s.io` namespace on all
three nodes before applying this overlay. Workloads use `imagePullPolicy:
Never` for DeerFlow and WeKnora PostgreSQL custom images. Provisioner-created sandbox Pods use
`IfNotPresent`, so the sandbox image must also exist on every schedulable node.

The WeKnora database uses
`deerflow.local/weknora-postgres:pg16-search-arm64-20260722-1`, built from the
verified ARM64 `local-pgvector:pg16-arm64` base plus pg_search 0.24.2. The
upstream ParadeDB PG17 image must not be restored on this cluster: its startup
path triggers `SIGILL` on the current CPUs. Keep both
`shared_preload_libraries=pg_search` and
`PGDATA=/var/lib/postgresql/data/pgdata`; the image was validated on datax-01
and the PostgreSQL local-PV node datax-02 with vector and BM25 queries.

When the skills source changes, build its seed image from the repository root
with this exact command so the image contains
`/seed/skills/public`, not a nested `/seed/skills/skills/public` directory:

```bash
docker build -f docker/k8s-skills/Dockerfile \
  -t deerflow.local/deer-flow-skills:20260722-enterprise-arm64-rc1 .
```

The skills-sync Job validates that layout before replacing the PVC contents.
For a registry-backed multi-node deployment, push an immutable architecture-
appropriate tag or digest and make it pullable from every node instead of
relying on per-node manual imports.

The MinerU adapter reuses the WeKnora docreader base image because it already
contains LibreOffice. Build it on an ARM64 server from the repository root:

```bash
docker build -f docker/mineru_adapter/Dockerfile \
  --build-arg DOCREADER_IMAGE=m.daocloud.io/docker.io/wechatopenai/weknora-docreader@sha256:65d9a4a4fb7b2d3c065eb8eabe033d1f001cedfd039be7f2355540ecad7da57b \
  -t deerflow.local/deer-flow-mineru-adapter:20260723-enterprise-arm64-rc1 .
```

Import the resulting image into the `k8s.io` containerd namespace on every
node where the adapter may run. The adapter is stateless, receives only an
internal ClusterIP Service, and translates WeKnora's `/file_parse` contract to
the enterprise PDF API. PPT/PPTX input is converted to PDF with LibreOffice
before it leaves the Pod.

## Storage

`storage-static.yaml` declares eight Retain local PVs. Three match the PVs
already present in the rebuilt cluster; create the other directories on their
assigned nodes before applying the bundle. PVCs set `volumeName` explicitly so
that similarly sized local volumes cannot bind to the wrong workload.

The PostgreSQL StatefulSet storage patch must retain
`accessModes: [ReadWriteOnce]` in its `volumeClaimTemplates` entry. Kustomize
replaces that list item's `spec` while applying the enterprise patch, so
omitting the access mode produces an invalid StatefulSet at API validation.

Local PV capacity is scheduling metadata, not a filesystem quota. Monitor the
shared `/opt` filesystem independently.

The checked-in static local PV topology intentionally pins the skills and user
data volumes to specific nodes. It is suitable for the audited three-node
layout but does not provide horizontally schedulable Gateway/sandbox storage.
Before scaling those Pods across nodes, migrate `deer-flow-skills-pvc` and
`deer-flow-userdata-pvc` to an RWX backend such as NFS or CephFS. Kubernetes
mount paths remain `/app/skills` in Gateway and `/mnt/skills` in sandboxes; only
the StorageClass/PV implementation changes.

Set `sandbox.shared_thread_data_mounts: true` only after Gateway and every
provisioner-created sandbox can mount that same user-data filesystem. This lets
the upload API write each file once instead of cold-starting a sandbox merely
to copy the bytes through its HTTP API. The current node-pinned local PV layout
can use the optimization only while Gateway and sandboxes remain co-located;
an actually horizontally scheduled deployment requires RWX storage.

The bundled Nginx gives thread upload routes a 600-second send/read timeout for
large files and slow storage. Any external Ingress, load balancer, WAF, or
server-side Nginx in front of NodePort `32026` must use a compatible request
body limit and timeout; the shortest timeout in the proxy chain remains the
effective limit.

## Runtime configuration

The source bundle must not contain real `.env`, `config.yaml`,
`extensions_config.json`, proxy credentials, API keys, or Kubernetes Secret
manifests. Create these runtime objects on `datax-01` immediately before the
deployment:

- `deer-flow-config` ConfigMap
- `deer-flow-extensions-config` ConfigMap
- `weknora-config` ConfigMap (generated by the WeKnora base kustomization)
- `deer-flow-env` Secret
- `deer-flow-postgres` Secret
- `weknora-secret` Secret
- `deer-flow-enterprise-ca` Secret, with the host's combined system and
  enterprise trust bundle stored under the `ca-bundle.crt` key
- `deer-flow-mineru` Secret, containing `MINERU_API_URL` and
  `MINERU_API_TOKEN`

Never print Secret YAML or decoded Secret values into an audit log.

Use the URL and hardcoded Bearer key from the approved enterprise
`simple_mineru_parser.py` example. Do not copy either value into this
repository, a Docker build argument, or a ConfigMap. Create the runtime Secret
interactively on the server:

```bash
read -r -p 'MinerU API URL: ' MINERU_API_URL
read -r -s -p 'MinerU API token: ' MINERU_API_TOKEN; echo
kubectl -n deer-flow create secret generic deer-flow-mineru \
  --from-literal=MINERU_API_URL="$MINERU_API_URL" \
  --from-literal=MINERU_API_TOKEN="$MINERU_API_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
unset MINERU_API_URL MINERU_API_TOKEN
```

The enterprise Gateway forces only `.ppt` and `.pptx` uploads to the `mineru`
engine and passes the in-cluster adapter endpoint as a per-upload override.
Other document types keep their native WeKnora parser selection. The local
overlay continues to use `markitdown`, so an internal MinerU outage cannot
break local development.

On SSO, the Gateway also saves the adapter Service URL as the WeKnora MinerU
default for every space the current user owns or administers. It reads and
merges the existing parser settings first, so OCR/table/model preferences are
preserved. The enterprise API URL and Key are never copied into WeKnora. A
viewer cannot rewrite another owner's space configuration, and an already
matching endpoint is not written again.

The enterprise overlay mounts `deer-flow-enterprise-ca` into `weknora-app` and
sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, and `CURL_CA_BUNDLE`. This is
required when the configured DashScope embedding or chat endpoint is inspected
by the enterprise TLS proxy. Create or update the Secret before applying the
overlay; otherwise the WeKnora App Pod cannot start. The Secret is mounted as a
projected directory rather than with `subPath`, so later CA rotations propagate
to the Pod filesystem automatically; restart the App to force long-lived HTTP
clients to rebuild their trust state immediately.

Generate distinct `PROVISIONER_API_KEY` and `SANDBOX_API_KEY` values. Configure
`sandbox.provisioner_api_key: $PROVISIONER_API_KEY` and
`sandbox.sandbox_api_key: $SANDBOX_API_KEY`; the Provisioner injects the latter
into Sandbox Pods through `deer-flow-env`. The RC2 Sandbox image also replaces
the broken upstream ARM64 `websocat` with checksum-pinned 1.14.1 and installs
CairoSVG for the PPT raster fallback.

## Validation

Render before applying:

```bash
kubectl kustomize k8s/enterprise > /tmp/deerflow-enterprise-rendered.yaml
grep -n 'nodePort:' /tmp/deerflow-enterprise-rendered.yaml
grep -n 'image:' /tmp/deerflow-enterprise-rendered.yaml
```

The first command must succeed, the NodePort check must show only `32026`, and
every registry-fetched third-party image must include an explicit `sha256`
digest. Locally built images use immutable release tags and `imagePullPolicy:
Never` after exact OCI checksum verification on every node.

After rollout, validate the adapter before uploading a presentation:

```bash
kubectl -n deer-flow rollout status deployment/deer-flow-mineru-adapter --timeout=180s
kubectl -n deer-flow run mineru-smoke --rm -i --restart=Never \
  --image=m.daocloud.io/docker.io/library/busybox@sha256:9532d8c39891ca2ecde4d30d7710e01fb739c87a8b9299685c63704296b16028 \
  -- wget -qO- http://deer-flow-mineru-adapter:50052/health
```

Then upload a real PPTX from the DeerFlow knowledge page and wait for its parse
status to become `completed`. A `200` health response proves only the adapter
is running; the real upload is required to verify server-network reachability,
the enterprise Key, LibreOffice conversion, and MinerU output together.
