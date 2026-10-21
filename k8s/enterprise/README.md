# Enterprise ARM64 deployment

This overlay records an earlier three-node Kylin ARM64 deployment. It is not
the current production desired state and must not be applied wholesale to the
expanded cluster. The [2026-10-21 live reconciliation](../../docs/pr13-current-production-reconciliation-20261021.md)
records the current replica/resource/image observations and remaining source
gaps. The historical node layout below is retained for provenance:

| Node             | Kubernetes name | Local storage                                        |
| ---------------- | --------------- | ---------------------------------------------------- |
| `192.168.111.2`  | `datax-01`      | DeerFlow home and skills                             |
| `192.168.111.37` | `datax-02`      | DeerFlow PostgreSQL, backups, and WeKnora PostgreSQL |
| `192.168.111.18` | `datax-03`      | WeKnora Redis, files, and docreader workspace        |

The overlay intentionally exposes only the shared DeerFlow Nginx service at
NodePort `32026`. WeKnora remains behind the same-origin
`/platform/knowledge-bases` route and is addressed by the stable
`weknora-frontend` Service DNS name.

## Security scan and configuration baseline

Follow [the remediation runbook](../../docs/security-scan-baseline-remediation.md). The shared Nginx template bounds client idle time and concurrency and restricts TLS 1.2 ciphers. Collect evidence separately for every report asset; repository configuration does not prove deployed patch status. Preserve required Kubernetes forwarding and NFS dependencies.

## Dashboard database isolation

The enterprise dashboard fails closed onto a physical PostgreSQL standby.
The DeerFlow primary remains on `datax-02`; `postgres-dashboard-replica` uses
the dedicated retained local PV
`/opt/deerflow-storage/dashboard-postgresql` on `datax-03`. Create that path and
the untracked `deer-flow-dashboard-postgres` Secret from
`dashboard-postgres.secret.example.yaml` before applying this overlay. Use
independent generated replication and dashboard passwords.

Gateway receives only the replica URL for dashboard routes and starts with
`DEER_FLOW_DASHBOARD_REQUIRE_READ_REPLICA=true`. It verifies both
`transaction_read_only=on` and `pg_is_in_recovery()=true` through a one-
connection pool. A missing, writable, primary, or unavailable target makes
only the admin dashboard return 503; the application database is never used as
a fallback, and chats, scheduled work, and digital employees continue on the normal
primary pool. The maintenance CronJob scans raw source rows on the standby and
uses its short-lived primary `NullPool` connection only for small daily/hourly
fact and cursor upserts. It is shipped suspended.

Do not enable the CronJob until migration `0017_dashboard_hourly_rollups`, the
daily backfill, the latest 72-hour backfill, replica readiness, and replay-lag
checks pass. The primary limits replication-slot WAL retention to 4 GiB so a
failed standby cannot exhaust its disk. See
[`docs/dashboard-rollup-operations.md`](../../docs/dashboard-rollup-operations.md)
for creation, verification, backfill, enablement, and rebuild commands.

The optional phase-1 iFLYTEK SkillHub workbench uses the same exposure model at
`/platform/skillhub/` and the stable `skillhub-web` Service name. Deploy the
reviewed upstream chart into the `deer-flow` namespace with
`k8s/skillhub/values-deer-flow.example.yaml` as the non-secret base, and keep
its public/web/API base paths aligned. SkillHub remains separately managed and
is not added to this Kustomize overlay, so its passwords, passive-session bridge
secret, database, Redis, scanner, and release lifecycle stay under the native
chart. The offline ARM64 release must import the customized server, upstream
web/scanner, PostgreSQL and exporter, Redis and exporter, and BusyBox waiter on
every eligible node before Helm install.
See `docs/skillhub-integration.md` before exposing the sidebar entry.

The Frontend deployment must override `WEKNORA_FRONTEND_URL` with the relative
same-origin path `/platform/knowledge-bases`. Do not use a loopback URL or pin a
node address: `127.0.0.1` resolves to the browser user's computer, while the
relative path continues to work when the public IP, DNS name, or TLS endpoint
changes.

The production WeKnora v0.8 UI must likewise exchange the DeerFlow session
through the same-origin `/api/weknora/sso-session` endpoint. Its SSO2 and
DOM-guard assets are built into the immutable image. The enterprise overlay
therefore removes the base v0.7 ConfigMap asset mounts; restoring those mounts
would overwrite the v0.8 entrypoint and can produce a blank or mixed-version
workspace. Restart `weknora-frontend` after changing the UI image or its runtime
configuration.

The base WeKnora deployment creates one personal workspace for each newly
provisioned DeerFlow user and disables ordinary self-service creation of
additional tenant workspaces. DeerFlow administrators remain cross-tenant
WeKnora system administrators. The embedded UI presents normal users with only
Knowledge Bases and Shared Spaces while keeping New Chat, Agents, Publish
Integrations, system settings, and tenant-management controls hidden; native
WeKnora RBAC and the server-side tenant-creation setting remain authoritative.

## Images

### PPT Master Agent model bridge

The isolated PPT Agent cannot call the enterprise `DeepSeek-V4-Flash` Chat
Completions endpoint as though it were a Responses provider. Gateway therefore
owns the narrow `/api/codex/deepseek/v1/responses` conversion endpoint. Create a
separate random `PPT_MASTER_RESPONSES_RELAY_KEY` in `deer-flow-env`; Gateway and
the `ppt-master-agent-host` receive that relay key, while only Gateway receives
the upstream `DeepSeek_V4_API_KEY`. The ordinary `sandbox` container is
deliberately excluded from the relay key.

The bridge follows the documented internal protocol: it forces streaming,
maps `enable_thinking` and `reasoning_effort`, reconstructs streamed function
calls, replays tool results with `tool_call_id`, bounds transient retries, and
does not persist or emit raw `reasoning_content`. Calls to `model.ai.cnooc`
bypass the external proxy with `trust_env=false` but retain normal CA and
hostname verification. Never copy the example client's `verify=False` setting.

After importing the Gateway, Provisioner, and PPT Sandbox release images on
every eligible node, acceptance requires a real Codex Shell tool round-trip
through this bridge. Unit tests or a simple HTTP 200 are necessary but not
sufficient evidence for the enterprise provider path.

Third-party images remain pinned to the ARM64 manifest digests verified on
`datax-01`. DeerFlow- and WeKnora-owned images use the immutable tags in
`release-baseline.yaml -> production_snapshot`; that inventory is the source of
truth for reproducing the accepted 2026-09-15 production capability baseline.
It deliberately keeps the main Gateway and the `ppt-master-runner` in separate
Deployments, Services, Pod failure domains, and image keys: the main Gateway
contains the digital-employee, Browser Live, UI2, and prompt-ABI fixes, while the
runner retains its separately accepted image. The runner requires three
consecutive PostgreSQL readiness samples before startup. Gateway uses a 4 GiB
request and 16 GiB limit in this overlay to preserve scheduling behavior while
adding headroom above the previously observed 10 GiB OOM boundary.
The same snapshot locks the WeKnora v0.8 App with AnyDoc, the v0.8 SSO UI,
Provisioner, Skills, MinerU Adapter, AIO Sandbox runtime, and the separately
managed SkillHub server/web images.

The snapshot represents production capability, not production state. It does
not contain Secret values, user data, database rows, PVC contents, or generated
OCI archives. See
[`docs/production-capability-reproduction-2026-09-15.md`](../../docs/production-capability-reproduction-2026-09-15.md)
for the exact checkout, image-supply, configuration, deployment, migration, and
acceptance sequence.

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
  -t deerflow.local/deer-flow-skills:20260901-enterprise-arm64-rc15-researchfund1 .
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
  --build-arg DOCREADER_IMAGE=m.daocloud.io/docker.io/wechatopenai/weknora-docreader@sha256:59e26f98f17c296c5f4bd216404558f51d31991b580cfef1d204903a01e4b46b \
  -t deerflow.local/deer-flow-mineru-adapter:20260915-enterprise-arm64-rc15-docxstability1 \
  docker/mineru_adapter
```

Import the resulting image into the `k8s.io` containerd namespace on every
node where the adapter may run. The adapter is stateless, receives only an
internal ClusterIP Service, and translates WeKnora's `/file_parse` contract.
The dispatcher labels interactive DeerFlow uploads with a `conversation` parse
profile while WeKnora knowledge-base work retains the `knowledge` profile.
Conversation DOCX first uses pinned `firecrawl-anydoc==0.1.9`; structurally
large knowledge-base DOCX may use the same bounded fast path without changing
ordinary knowledge-base parser rules. The Adapter inspects compressed,
expanded, and `word/document.xml` sizes before parsing. Large DOCX bypasses
DocReader's `StdMarkitdownParser`, uses the official v0.8 native `DocxParser`,
and retains an isolated low-memory XML text recovery after richer native
parsing fails. This handles documents that correctly trip AnyDoc's fixed XML
node safety limit without weakening that limit. Thin, failed, or timed-out
output continues through the existing LibreOffice and MinerU fallbacks. Legacy
DOC and ordinary knowledge-base DOCX keep the existing native-first route. A
non-empty result is published immediately. Operators may enable
`MINERU_WORD_PDF_VISUAL_SUPPLEMENT_ENABLED=true` for a best-effort supplement,
but its failure cannot invalidate already recovered native text, tables, and
images. AnyDoc and DocReader run in bounded child processes so a malformed Word
file cannot fail the worker's liveness endpoint. Presentation conversion
remains strict. PDF input is inspected locally before parsing: documents
whose pages all contain usable text stay on the WeKnora builtin text parser,
while a scanned/image-dominated page routes the whole PDF to the enterprise
MinerU API. For searchable PDF/Word pages, multilingual `Figure N`/`图 N`
detection plus image/vector geometry identifies pages that need a retrievable
full-page visual-evidence image. Each image stays beside its page number and
caption/context so chunking does not detach the resource from its retrieval
semantics. Inline images returned by MinerU are decoded into WeKnora's
persistent `images` map and rewritten to stable Markdown/HTML references.
Scanned pages and converted presentation slides remain referenced source-page
JPEGs. Both branches therefore return the same Markdown plus `images` contract
for VLM processing and question answering.

The adapter's HTTP server remains threaded for probes and request intake, but
`ParseCoordinator` admits exactly one PDFium/DocReader parse per worker. A busy
worker returns retryable HTTP 429 instead of accumulating a hidden in-process
queue; durable waiting belongs to WeKnora Redis/Asynq and the dispatcher. This
prevents allocator corruption and one Pod retaining many simultaneous request
bodies. `/health` remains an observability endpoint, `/live` proves that the
HTTP process responds and owns Kubernetes readiness/liveness, and `/ready`
returns 503 while the worker is parsing. The dispatcher probes `/ready` before
assignment and skips busy workers while they remain healthy members of the
headless discovery Service. It quarantines ambiguous timeout/5xx workers until
they prove idle again. Do not bypass this admission boundary or enable
concurrent parsing inside one adapter process.

LibreOffice is launched in a dedicated process group. On timeout, the adapter
terminates the complete group, escalates to SIGKILL after a short grace period,
and reaps adopted descendants through Linux child-subreaper mode. It also
cleans up a daemonized descendant after an apparently successful launcher exit.
This prevents orphaned `soffice.bin` processes and zombies from consuming the
4 GiB worker cgroup until the Pod is OOM-killed.

The audited three-node cluster runs exactly six serialized adapter workers,
spread two per node. A separate dispatcher owns the bounded cross-Pod queue,
round-robins free workers, and lets interactive DeerFlow uploads move ahead of
knowledge-base batch work that has not started. The WeKnora App remains a single
replica with `WEKNORA_ASYNQ_CORE_CONCURRENCY=4` and
`WEKNORA_ASYNQ_SHARED_CONCURRENCY=2`: at most six document parse calls can enter
the dispatcher, and additional uploads remain durable in the Redis/Asynq
`default` queue. Do not raise those values without adding reviewed serialized
worker capacity and passing the 3/10/30-document acceptance matrix. Worker
topology uses `maxSkew: 1`; its disruption budget keeps four workers available,
and the dispatcher has its own `minAvailable: 1` budget. The single dispatcher
uses `Recreate` updates because its busy/quarantine state is process-local; two
overlapping dispatcher replicas could otherwise assign the same worker.
WeKnora 0.8.0 drains downstream `summary`, `multimodal`, `graph`, and `question`
queues through an independent enrichment pool. Enterprise deployments keep
`WEKNORA_ASYNQ_POSTPROCESS_CONCURRENCY=2`,
`WEKNORA_ASYNQ_ENRICHMENT_CONCURRENCY=12`, and
`WEKNORA_ASYNQ_MAINTENANCE_CONCURRENCY=4`; this prevents image OCR/caption
fan-out from being constrained by the two parser workers without admitting
additional MinerU calls. Raise enrichment only after checking the VLM/LLM
provider quota and queue latency.

The `weknora-enterprise-mineru-defaults-v2` Job applies
`weknora-mineru-defaults.sql` once. It routes platform defaults for
PDF/DOC/DOCX/PPT/PPTX through the adapter for existing and future knowledge
bases, while leaving explicit non-default parser engines and later user edits
authoritative. Documents indexed before this Job or adapter release must be
re-parsed; missing image bytes cannot be reconstructed during question
answering.

## Storage

`storage-static.yaml` declares nine Retain local PVs. Three match the PVs
already present in the rebuilt cluster; create the other directories on their
assigned nodes before applying the bundle. PVCs set `volumeName` explicitly so
that similarly sized local volumes cannot bind to the wrong workload.

Create `/opt/deerflow-storage/weknora-neo4j` on `datax-03` with the same
container-write ownership used for the other WeKnora data directories before
applying the bundle. The single Neo4j Community StatefulSet is node-pinned by
its local PV; a multi-node HA deployment requires a managed graph database or
licensed Neo4j cluster rather than additional replicas sharing this RWO path.

The PostgreSQL StatefulSet storage patch must retain
`accessModes: [ReadWriteOnce]` in its `volumeClaimTemplates` entry. Kustomize
replaces that list item's `spec` while applying the enterprise patch, so
omitting the access mode produces an invalid StatefulSet at API validation.

Local PV capacity is scheduling metadata, not a filesystem quota. Monitor the
shared `/opt` filesystem independently.

WeKnora's `STORAGE_TYPE=local` stores original uploads and parser-produced image
assets under `/data/files`, backed by the `weknora-data-files` PVC (the
enterprise static PV is `/opt/deerflow-storage/weknora-files`). This backend is
fully capable of retaining Word/PDF/PPT images and their document references;
an object-storage engine is not required for that feature. Move this PVC to an
HA object store or RWX filesystem only when durability, independent backups,
multi-node rescheduling, or horizontal `weknora-app` replicas require it. The
temporary `weknora-docreader-tmp` PVC is parser scratch space and is not the
authoritative image store.

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

For this audited node-pinned enterprise overlay, the setting is mandatory and
is enforced by `scripts/validate_enterprise_release.py` and
`scripts/verify_enterprise_runtime.sh`. A hotfix that replaces
`deer-flow-config` must preserve it and must restart Gateway because the ConfigMap
is mounted with `subPath`; otherwise uploads silently fall back to Sandbox API
copying and can block for the full Sandbox startup timeout.

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

The enterprise runtime patch sets `DEER_FLOW_SSO_ONLY=true` on both Gateway and
Frontend and sets the Frontend's `DEER_FLOW_UNAUTHENTICATED_HOME_URL` to the
approved DataLake portal. The exact root remains a public landing page; its
Login, New Chat, and Get Started actions enter the server-side auth route. This
combination redirects every unauthenticated auth page away from DeerFlow and
makes Gateway return `403 local_auth_disabled` for local login, registration,
setup initialization, password changes, and generic OIDC flows. Do not remove
either variable or depend on the browser redirect alone;
`/api/v1/auth/cnooc/login` is the only interactive enterprise sign-in entry that
remains enabled.

- `deer-flow-config` ConfigMap
- `deer-flow-extensions-config` ConfigMap
- `weknora-config` ConfigMap (generated by the WeKnora base kustomization)
- `deer-flow-env` Secret
- `deer-flow-postgres` Secret
- `weknora-secret` Secret
- `weknora-neo4j-secret` Secret, containing only `NEO4J_PASSWORD`
- `deer-flow-enterprise-ca` Secret, with the host's combined system and
  enterprise trust bundle stored under the `ca-bundle.crt` key
- `deer-flow-mineru` Secret, containing `MINERU_API_URL` and
  `MINERU_API_TOKEN`

The production `deer-flow-config` may formally enable user-owned Skill creation
with the following non-secret runtime section. Keep the moderation model valid
and available; scanner failure remains fail-closed. The Gateway user-data mount
must remain persistent because created skills live below
`.deer-flow/users/<user_id>/skills/custom/` and are intentionally isolated by
the authenticated user identity.

```yaml
skill_evolution:
  enabled: true
  moderation_model_name: deepseek-v4-flash
```

Never print Secret YAML or decoded Secret values into an audit log.
Store `QWEN_ASR_API_KEY` in `deer-flow-env` when main-composer voice input is
enabled. Gateway calls the enterprise Qwen3-ASR stream directly and Nginx
forwards `/api/voice-input/stream` as a WebSocket. The public browser origin
must be a secure context; terminate TLS in front of NodePort `32026`, or deploy
an audited browser policy that treats only the exact intranet origin as secure.
Plain HTTP without that policy cannot access the microphone even when the
Gateway and model are healthy.
When Haineng or collaboration click telemetry is enabled, add a separately
generated `EXTERNAL_AGENT_CLICK_INGEST_KEY` to `deer-flow-env`. Trusted
server-side collectors submit events to `POST /api/agent_click` using that key;
never embed it in browser JavaScript or a ConfigMap. The supported source names
are `海能平台` and `协同平台`.

The enterprise overlay injects both proxy-variable cases into generated
Sandbox Pods, but bypasses the interception proxy only for the audited direct
HTTPS destinations used by GitHub. Keep those exact hosts/provider suffixes
in both `NO_PROXY` and `no_proxy`. DashScope API calls, Aliyun
generation-result downloads, and Tavily search/extract calls must continue
through the verified enterprise proxy: direct root GETs work, but authenticated POSTs are
reset on every audited node. All other external destinations also continue
through the enterprise proxy. If a node-specific certificate-chain failure
appears for an arbitrary destination, fix the proxy chain instead of adding a
broad domain or CIDR bypass.

Create the graph database credential interactively on the server:

```bash
read -r -s -p 'Neo4j password: ' NEO4J_PASSWORD; echo
kubectl -n deer-flow create secret generic weknora-neo4j-secret \
  --from-literal=NEO4J_PASSWORD="$NEO4J_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -
unset NEO4J_PASSWORD
```

Use the approved MinerU-2605 service root URL (for example, ending in
`/member1/mineru-2605`) and its Bearer key. The Adapter derives the asynchronous
`/tasks` routes from that root; legacy full `/file_parse` and `/tasks` URLs
are normalized back to the reviewed service root. Do not copy either value into this repository, a Docker build
argument, or a ConfigMap. Create the runtime Secret interactively on the server:

```bash
read -r -p 'MinerU API URL: ' MINERU_API_URL
read -r -s -p 'MinerU API token: ' MINERU_API_TOKEN; echo
kubectl -n deer-flow create secret generic deer-flow-mineru \
  --from-literal=MINERU_API_URL="$MINERU_API_URL" \
  --from-literal=MINERU_API_TOKEN="$MINERU_API_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
unset MINERU_API_URL MINERU_API_TOKEN
```

The enterprise Adapter uses MinerU's asynchronous multipart `POST /tasks`
workflow with `backend=pipeline`, `parse_method=auto`, Markdown and images
enabled, and a bounded 1800-second end-to-end timeout. Keep
`MINERU_REQUEST_MODE=async`; no native WeKnora change is required because the
Adapter keeps its caller-facing `/file_parse` response open while it internally
polls the accepted task. It submits exactly once, polls the same task every
three seconds, and retrieves that task's result. Transient business code `20006`
or short-lived status/result HTTP 404 responses receive at most 20 bounded
same-task retries; they never trigger a duplicate task submission or an
automatic synchronous fallback. The synchronous 20-page batch settings remain
in the Deployment only as an explicit emergency rollback path. Before Markdown is
returned to WeKnora, an implausibly large ATX heading is demoted to ordinary
text without deleting content or image references, preventing that malformed
heading from being copied into every child embedding request. Keep
`MINERU_TRUST_ENV=false` for the approved
direct internal route. Because Requests does not consume
`REQUESTS_CA_BUNDLE` through environment merging when `trust_env` is disabled,
the Adapter explicitly binds that mounted CA bundle to its MinerU session while
retaining certificate and hostname verification. When exposed by the provider,
the MinerU `/health` response must report at least 20 concurrent upstream
requests before rollout. Some reviewed gateway prefixes do not publish
`/health`; in that case a real authenticated asynchronous PDF parse is the hard
preflight gate. This upstream capacity does not
authorize in-process PDFium/DocReader concurrency; retain the six serialized
Adapter workers, dispatcher, and WeKnora's 4+2 worker limits unless a separate
memory and native-library safety review approves a different topology.

The enterprise Gateway routes `.pdf`, `.ppt`, and `.pptx` uploads to the
in-cluster adapter and passes its endpoint as a per-upload override. Only
scanned/mixed PDFs and presentations call the enterprise MinerU API; searchable
PDFs are parsed locally by WeKnora. Other document types keep their native
WeKnora parser selection. The local overlay continues to use `markitdown`, so
an internal MinerU outage cannot break local development.

Native XLSX parsing remains in the official WeKnora DocReader Pod. The base
bundle mounts `weknora-docreader-xlsx-compat` through `PYTHONPATH` so workbooks
containing the formatting-only `defaultColWidthPt` worksheet extension can be
opened by the pinned openpyxl runtime. The shim ignores no other attributes and
does not mutate the original workbook. After applying or updating the
ConfigMap, restart `deployment/weknora-docreader` and validate both a standard
XLSX and a representative affected XLSX before retrying failed documents.

URL knowledge imports also remain in the official DocReader Pod. The enterprise
overlay injects `DOCREADER_EXTERNAL_HTTP_PROXY`,
`DOCREADER_EXTERNAL_HTTPS_PROXY`, and the mounted enterprise CA paths. The
source-mounted compatibility hook handles server-rendered pages before WebKit,
revalidates every redirect with the upstream SSRF policy, and treats the
upstream `Error parsing web page` sentinel as a real failure. This prevents
false `completed` records containing only diagnostic text. Re-import one real
server-rendered page (including a representative WeChat article) and one
dynamic page after rollout; each successful record must contain article/page
content, and no chunk may begin with that sentinel.

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

The enterprise overlay mounts `deer-flow-enterprise-ca` into the Gateway,
`weknora-app`, and MinerU Adapter and sets `SSL_CERT_FILE`,
`REQUESTS_CA_BUNDLE`, and `CURL_CA_BUNDLE` directly to
`/etc/deerflow-ca/ca-bundle.crt`. This is required when an external model or
parser endpoint is inspected by the enterprise TLS proxy. Create or update the
Secret before applying the overlay; otherwise the affected Pods cannot start.
Do not mount the Secret over the image's conventional
`/etc/ssl/certs/ca-certificates.crt` path: that path can be a distribution-owned
symlink and is fragile across image upgrades and Kubernetes `subPath` mounts.
Restart the affected Deployments after CA rotation so long-lived HTTP clients
reload the projected bundle.

The enterprise `Qwen3-Embedding-8B` and `Qwen3-Reranker-8B` built-ins call
authenticated in-cluster Gateway relays. Keep `CNOOC_RETRIEVAL_API_KEY` as the
upstream credential and generate a different
`WEKNORA_INTERNAL_MODEL_RELAY_KEY` for WeKnora-to-Gateway authentication.
Gateway reaches `model.ai.cnooc` directly with `trust_env=false`, fixes
embeddings at 1024 Matryoshka dimensions, and L2-normalizes any
4096-dimensional native response after prefix truncation. The Gateway
embedding relay is capped at 200 concurrent upstream requests to match the
approved retrieval credential. This does not change WeKnora's 4+2 document
worker limit or the six serialized MinerU Adapter workers; rerank continues
through its independent relay path and shares the same upstream application
quota. Bounded retries for transient business code `10020` remain enabled. The versioned
WeKnora defaults Job applies
`k8s/weknora/enterprise-model-feature-defaults.sql` after the built-in model
rows are ready. It switches only empty/future knowledge bases automatically.
Populated knowledge bases require a controlled re-parse and success gate before
the old external embedding/chat/rerank rows are hidden.

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

The enterprise ARM64 overlay also passes `DISABLE_BROWSER=true` and
`DISABLE_MCP_BROWSER=true` into every Provisioner-created AIO Sandbox. The
pinned AIO image's embedded Chromium GPU process is not compatible with the
production kernel and otherwise enters a one-second supervisor restart loop.
This policy does not disable DeerFlow's `browser_*` tools or Browser Live: those
use the Gateway's process-local Playwright Chromium and must remain configured
without an external `cdp_url`. Retire browser-enabled Sandbox Pods only in a
zero-active-run maintenance window while respecting their cross-process
execution leases; PVC-backed user data remains intact and replacement Sandboxes
are created lazily.

## Release baseline gates

`k8s/enterprise/release-baseline.yaml` is the machine-readable inventory of
enterprise controls and manual acceptance tests. Keep every historical fix in
one of the owned persistence layers listed there: source, immutable image,
Kubernetes manifest, runtime ConfigMap/Secret reference, idempotent database
Job, storage, or operations. A script under `outputs/` and an ad-hoc live
`kubectl patch` are recovery aids, not release sources of truth.

Do not create `deer-flow-config` from an unvalidated `config.yaml`. The six
Sandbox command proxy entries must reference the Gateway's runtime environment;
literal empty strings override the correct proxy environment already present in
the Sandbox Pod and force authenticated DashScope POSTs onto the blocked direct
route:

The enterprise Gateway manifest sets
`DEERFLOW_UPLOAD_DOCUMENT_PARSE_TIMEOUT_SECONDS=570`, while the dispatcher uses
a 45-second admission wait and a 550-second interactive request timeout. All
three remain below Nginx's 600-second upload timeout. Conversation Word fallback
stages are independently bounded at 120 seconds. The Adapter therefore returns
success or a controlled parser failure before Nginx can emit 504. Enterprise
also sets
`DEERFLOW_UPLOAD_DOCUMENT_LOCAL_FALLBACK_AFTER_ENTERPRISE_ENABLED=false` so
Gateway does not repeat another unbounded local parse after the Adapter has
already exhausted AnyDoc, DocReader, LibreOffice, and MinerU. The upstream-
compatible code default still permits local conversion where the complete
enterprise Adapter is not deployed.

The production Gateway resource baseline is requests `2 CPU / 4Gi` and limits
`4 CPU / 16Gi`. This reserves CPU for the single Gateway endpoint during
WeKnora parser traffic and bounded PostgreSQL maintenance. Treat an ad-hoc
resource patch as incident recovery only: update `runtime-patches.yaml` and its
release test before the next declarative rollout, or the next apply can silently
restore the lower pre-incident CPU settings.

```yaml
sandbox:
  environment:
    HTTP_PROXY: $HTTP_PROXY
    HTTPS_PROXY: $HTTPS_PROXY
    NO_PROXY: $NO_PROXY
    http_proxy: $HTTP_PROXY
    https_proxy: $HTTPS_PROXY
    no_proxy: $NO_PROXY
    SSL_CERT_FILE: $SSL_CERT_FILE
    REQUESTS_CA_BUNDLE: $REQUESTS_CA_BUNDLE
    CURL_CA_BUNDLE: $CURL_CA_BUNDLE
    NODE_EXTRA_CA_CERTS: $NODE_EXTRA_CA_CERTS
    OPENSSL_CONF: $OPENSSL_CONF
    DASHSCOPE_TLS_COMPAT: $DASHSCOPE_TLS_COMPAT
```

Run the tracked offline release gate against the exact `config.yaml` that will
be packaged and applied:

```bash
python scripts/validate_enterprise_release.py \
  --config /path/to/release/config.yaml \
  --manifest k8s/enterprise/runtime-patches.yaml
```

The command must end with `enterprise_release_baseline=PASS`. It fails closed
for blank or stale command-level proxy overrides, missing Sandbox proxy/CA
injection, Gemini reintroduction, external provider proxy bypasses, or missing
Gateway TLS compatibility settings. The full gate also validates the
DeepSeek-only runtime policy, scheduled-task limits, immutable ARM64 images,
PG16/pg_search database pin, static storage bindings, three-node MinerU
availability, Neo4j, internal WeKnora defaults and narrow SSRF allowlist,
same-origin Nginx route ordering, the single public NodePort, and required
security source boundaries. It validates references and policy only; it never
reads Secret values.

After rollout, copy the read-only runtime gate to `datax-01` and run:

```bash
sudo KUBECONFIG_FILE=/etc/kubernetes/admin.conf \
  /bin/bash scripts/verify_enterprise_runtime.sh
```

The command must end with `enterprise_runtime_baseline=PASS`. This check uses
the Gateway's own dependency environment to verify the mounted ConfigMap,
resolved proxy sources, Provisioner contract, CA bundle, OpenSSL compatibility
configuration, three ARM64 nodes, workload readiness, live image pins, bound
PVCs, MinerU distribution/PDB, WeKnora graph/concurrency/SSRF settings, and
NodePort without printing credentials. Complete every manual acceptance item in
`release-baseline.yaml`, including one new-conversation `image-generation`
request after the gate; `kubectl exec` alone does not exercise the per-command
environment injected by DeerFlow's Sandbox API. Local functional testing may
use a separate Bailian-only test configuration when the internal DeepSeek
endpoint is unreachable, but that model must not be copied into the enterprise
release config. Complete the listed browser, security, Neo4j, and 3/10/30
document parser-load acceptance cases before production sign-off.

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
kubectl -n deer-flow get pods -l app=deer-flow-mineru-adapter \
  -o custom-columns='POD:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount'
kubectl -n deer-flow run mineru-smoke --rm -i --restart=Never \
  --image=m.daocloud.io/docker.io/library/busybox@sha256:9532d8c39891ca2ecde4d30d7710e01fb739c87a8b9299685c63704296b16028 \
  -- wget -qO- http://deer-flow-mineru-adapter:50052/health
```

The Pod listing must show one Ready adapter on each of `datax-01`, `datax-02`,
and `datax-03`, with zero restarts. Verify the WeKnora capacity gate without
printing any Secret values:

```bash
kubectl -n deer-flow get deployment weknora-app \
  -o jsonpath='{range .spec.template.spec.containers[?(@.name=="app")].env[*]}{.name}={.value}{"\n"}{end}' \
  | grep -E '^WEKNORA_(ASYNQ_CORE_CONCURRENCY|ASYNQ_POSTPROCESS_CONCURRENCY|ASYNQ_ENRICHMENT_CONCURRENCY|ASYNQ_MAINTENANCE_CONCURRENCY|ASYNQ_SHARED_CONCURRENCY|DOCREADER_CALL_TIMEOUT|DOCUMENT_PROCESS_TIMEOUT)='
```

Then upload three real files from the DeerFlow knowledge page: one searchable
PDF, one scanned PDF, and one PPTX. Wait for each parse status to become
`completed`, and inspect adapter logs for `route=builtin` on the searchable PDF
and `route=mineru` on the scanned PDF. A `200` health response proves only the
adapter is running; the real uploads are required to verify local routing,
server-network reachability, the enterprise Key, LibreOffice conversion, and
MinerU OCR together.

Capacity acceptance is staged rather than performed as an uncontrolled
production flood:

1. Upload three representative files together. Confirm three Pods can each
   report `parser.active=1`, every document completes, and restarts remain zero.
2. Upload ten mixed files together. Confirm no allocator signature appears,
   completed count reaches ten, and pending depth eventually returns to zero.
3. Upload thirty representative files together. Three may parse concurrently;
   the remaining tasks must wait in Redis/Asynq and drain without manual
   resubmission. Measure total drain time and verify no document exceeds the
   30-minute DocReader call or 2-hour document-task ceiling.

During the test, inspect the WeKnora System Admin runtime queue dashboard (or
its read-only runtime queue API) together with each adapter Pod's `/health`.
Abort the test if any Pod restarts, becomes OOMKilled, logs native allocator
errors, or if `parser.waiting` grows persistently: that indicates the upstream
three-call gate is no longer aligned with adapter capacity.

For repeatable diagnosis from the DeerFlow origin, SystemAdmin users can read
`GET /api/weknora/admin/runtime/queues`; it forwards the upstream WeKnora queue
snapshot without exposing a platform API key. Use
`GET /api/weknora/admin/runtime/queues/{queue}/tasks?state=pending&page_size=100`
to inspect the oldest pending tasks. Interpret the result together with
`parser.waiting`: a growing `default` queue with low Adapter wait indicates the
WeKnora worker budget is the limiter; low `default` depth with growing
`parser.waiting` indicates Adapter capacity or uneven Service distribution;
growing `summary`/`multimodal`/`graph`/`question` queues indicates downstream
model or provider capacity. Do not raise worker concurrency until the queue's
downstream dependency has headroom.

The Gateway proxy bypass list includes `model.ai.cnooc` in both `NO_PROXY` and
`no_proxy`. Internal model endpoints on that host must be reached directly;
routing them through the enterprise outbound proxy can leave streamed chat
requests waiting until the proxy resets the connection.

The audited outbound TLS proxy can present a CA-validated certificate whose
legacy RSA key is rejected by OpenSSL 3 at the default security level. The
enterprise overlay mounts `gateway-openssl-compat.yaml` only into the Gateway
Pod and sets `OPENSSL_CONF` there with `DEFAULT:@SECLEVEL=1`. It also enables
`WEKNORA_BAILIAN_TLS_COMPAT`, which gives only the authenticated Bailian
embedding/ASR adapter clients an explicit SECLEVEL=1 SSL context, and enables
`TAVILY_TLS_COMPAT` for DeerFlow's Tavily-only verified HTTPS session.
Certificate chain and hostname verification remain enabled; do not replace
this with `verify=false`, `--insecure`, or a cluster-wide OpenSSL downgrade.
Remove these compatibility settings after the enterprise proxy certificate is
upgraded to a key accepted by OpenSSL's default policy.
