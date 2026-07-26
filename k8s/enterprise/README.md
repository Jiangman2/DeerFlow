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
`datax-01` on 2026-07-21. Provisioner, Skills, and Sandbox remain on the
immutable `20260723-enterprise-arm64-rc3` release tag. The MinerU adapter
remains on the validated `20260723-enterprise-arm64-rc4` tag. Gateway uses
`20260726-enterprise-arm64-rc6-embeddingrelay2`, while Frontend uses
`20260725-enterprise-arm64-rc6-schedulermodel1`. These immutable application
tags include the current CNOOC SSO, WeKnora integration, direct agent-card
routes, scheduled-task model selection, and the authenticated embedding relay
required by the enterprise deployment:

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
  -t deerflow.local/deer-flow-skills:20260723-enterprise-arm64-rc3 .
```

The skills-sync Job validates that layout before replacing the PVC contents.
For a registry-backed multi-node deployment, push an immutable architecture-
appropriate tag or digest and make it pullable from every node instead of
relying on per-node manual imports.

The MinerU adapter reuses the WeKnora docreader base image because it already
contains LibreOffice. Build it on an ARM64 server with
`docker/mineru_adapter` as the build context:

```bash
docker build -f docker/mineru_adapter/Dockerfile \
  --build-arg DOCREADER_IMAGE=m.daocloud.io/docker.io/wechatopenai/weknora-docreader@sha256:65d9a4a4fb7b2d3c065eb8eabe033d1f001cedfd039be7f2355540ecad7da57b \
  -t deerflow.local/deer-flow-mineru-adapter:20260723-enterprise-arm64-rc4 \
  docker/mineru_adapter
```

Import the resulting image into the `k8s.io` containerd namespace on every
node where the adapter may run. The adapter is stateless, receives only an
internal ClusterIP Service, and translates WeKnora's `/file_parse` contract.
PPT/PPTX input is converted to PDF with LibreOffice. PDF input is inspected
locally before parsing: documents whose pages all contain usable text stay on
the WeKnora builtin parser bundled in the image; a scanned/image-dominated page
routes the whole PDF to the enterprise MinerU API. Inline Base64 images in
MinerU markdown are replaced with a compact marker before WeKnora chunks and
embeds the result.

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

The enterprise Gateway routes `.pdf`, `.ppt`, and `.pptx` uploads to the
in-cluster adapter and passes its endpoint as a per-upload override. Only
scanned/mixed PDFs and presentations call the enterprise MinerU API; searchable
PDFs are parsed locally by WeKnora. Other document types keep their native
WeKnora parser selection. The local overlay continues to use `markitdown`, so
an internal MinerU outage cannot break local development.

The PDF detector defaults match WeKnora's existing scanned-page heuristics:
`PDF_SCAN_IMAGE_RATIO=0.5`, `PDF_SCAN_MIN_CHARS_PER_PAGE=10`, and
`PDF_SCAN_LOW_TEXT_IMAGE_RATIO=0.1`. A full-page image remains a scanned page
even when the PDF also carries a hidden OCR text layer. Tune these values only
after testing representative enterprise PDFs.

On SSO, the Gateway also saves the adapter Service URL as the WeKnora MinerU
default for every space the current user owns or administers. It reads and
merges the existing parser settings first, so OCR/table/model preferences are
preserved. The enterprise API URL and Key are never copied into WeKnora. A
viewer cannot rewrite another owner's space configuration, and an already
matching endpoint is not written again.

After the WeKnora database schema exists, apply
`k8s/enterprise/weknora-mineru-defaults.sql` once with `psql`. It installs an
idempotent database trigger that adds any missing PDF/PPT/PPTX types to an
adapter rule when a native WeKnora UI flow creates or updates a knowledge base.
An explicitly configured parser for any of those file types is preserved. The
trigger is stored in the persistent WeKnora database, so application restarts
and rolling releases do not remove it; reapply the SQL after provisioning a new
database or after upgrading an older trigger.

The enterprise overlay mounts `deer-flow-enterprise-ca` into `weknora-app` and
sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, and `CURL_CA_BUNDLE` directly to
`/etc/deerflow-ca/ca-bundle.crt`. This is required when the configured
external model or parser endpoint is inspected by the enterprise TLS proxy.
Create or update the Secret before applying the overlay; otherwise the WeKnora
App Pod cannot start. Do not mount the Secret over the image's conventional
`/etc/ssl/certs/ca-certificates.crt` path: that path can be a distribution-owned
symlink and is fragile across image upgrades and Kubernetes `subPath` mounts.
Restart the App after CA rotation so long-lived HTTP clients reload the
projected bundle.

The enterprise `text-embedding-v4` built-in must use
`http://gateway:8001/api/weknora/bailian/v1` with provider `generic`.
WeKnora sends `/embeddings` to that in-cluster endpoint; Gateway authenticates
the shared embedding key and relays the request through its scoped enterprise
proxy/CA/OpenSSL compatibility path. This avoids the interception proxy's
alternating certificate profiles without disabling TLS verification. Reapply
`k8s/weknora/enterprise-model-feature-defaults.sql` to migrate an existing
model row.

The WeKnora App's uppercase and lowercase proxy bypass lists must include
`deer-flow-mineru-adapter`, the other WeKnora Service names, `.svc`, and
`.cluster.local`. Otherwise its proxy-aware HTTP transport can send the
in-cluster `/file_parse` request to the enterprise proxy, which may redirect it
to a DNS-failure IP that WeKnora correctly rejects through SSRF validation.
Its comma-separated `SSRF_WHITELIST_EXTRA` must retain
`deer-flow-mineru-adapter`, the exact internal model hostname
`model.ai.cnooc`, and the authenticated in-cluster `gateway` relay Service.
These names resolve to approved private service addresses,
which WeKnora intentionally rejects unless explicitly allowlisted. Do not
replace the exact hostname with a broad private CIDR or disable SSRF checks.
Additional internal embedding and rerank providers should normally be exposed
through authenticated Gateway adapters. That keeps provider credentials,
enterprise CA/proxy behavior, timeouts, and upstream-specific request mapping
in one server-side boundary, while WeKnora only calls the stable `gateway`
Service. When a provider cannot use that adapter boundary and WeKnora must call
it directly, add its reviewed exact hostname to `SSRF_WHITELIST_EXTRA`,
`NO_PROXY`, and `no_proxy` together.

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

Then upload three real files from the DeerFlow knowledge page: one searchable
PDF, one scanned PDF, and one PPTX. Wait for each parse status to become
`completed`, and inspect adapter logs for `route=builtin` on the searchable PDF
and `route=mineru` on the scanned PDF. A `200` health response proves only the
adapter is running; the real uploads are required to verify local routing,
server-network reachability, the enterprise Key, LibreOffice conversion, and
MinerU OCR together.

The Gateway proxy bypass list includes `model.ai.cnooc` in both `NO_PROXY` and
`no_proxy`. Internal model endpoints on that host must be reached directly;
routing them through the enterprise outbound proxy can leave streamed chat
requests waiting until the proxy resets the connection.

The audited outbound TLS proxy can present a CA-validated certificate whose
legacy RSA key is rejected by OpenSSL 3 at the default security level. The
enterprise overlay mounts `gateway-openssl-compat.yaml` only into the Gateway
Pod and sets `OPENSSL_CONF` there with `DEFAULT:@SECLEVEL=1`. It also enables
`WEKNORA_BAILIAN_TLS_COMPAT`, which gives only the authenticated Bailian
embedding/ASR adapter clients an explicit SECLEVEL=1 SSL context. Certificate
chain and hostname verification remain enabled; do not replace this with
`verify=false`, `--insecure`, or a cluster-wide OpenSSL downgrade. Remove this
compatibility ConfigMap after the enterprise proxy certificate is upgraded to
a key accepted by OpenSSL's default policy.
