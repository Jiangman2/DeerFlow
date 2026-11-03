# WeKnora local Kubernetes deployment

This manifest runs WeKnora inside the same `deer-flow` namespace as DeerFlow so
Gateway can call it through the stable in-cluster URL:

```text
http://weknora-app:8080
```

The WeKnora application and UI images are pinned by digest to the v0.7.0
release baseline used by this integration. Do not replace either image with the
mutable `latest` tag. The UI digest must remain the official v0.7.0
multi-platform manifest: earlier UI content does not understand the
`resource://`/request-local image handles emitted by the v0.7.0 App and leaves
retrieved knowledge images in the failed-preview state. The enterprise overlay
intentionally substitutes its ARM64 App image rebuilt from the same baseline
with the required proxy compatibility patch.

Original documents and parser-generated image assets are stored under
`/data/files` on the `weknora-data-files` PVC. Word, searchable PDF, and native
presentation parsers return Markdown references plus an `images` map, which the
App persists through this configured storage backend. Local PVC storage supports
this behavior without another storage engine. Object storage or RWX storage is
an operational upgrade for HA, backup, and multi-replica scheduling, not a
prerequisite for retaining document images. `weknora-docreader-tmp` is temporary
parser workspace and is not the durable asset store.

The official DocReader image remains pinned and unmodified. The
`weknora-docreader-xlsx-compat` ConfigMap mounts small `sitecustomize` hooks at
`/opt/deerflow-compat`. The XLSX hook ignores only the non-standard,
formatting-only `defaultColWidthPt` worksheet attribute emitted by some
enterprise exporters. The URL hook first fetches server-rendered HTTP(S) HTML,
validates every redirect through WeKnora's SSRF checker, and retains Playwright
for dynamic pages. The static client has a 10 MiB response ceiling, ignores
ambient proxy variables, and uses only DocReader's dedicated proxy plus a
verified CA bundle. Browser/static hard failures raise a parser error instead
of publishing `Error parsing web page` as valid knowledge. `/dev/shm` is a
bounded 256 MiB memory-backed volume for the remaining browser fallback. File
parsers and uploaded files are otherwise unchanged. Restart
The webpage hook is registered as a one-shot post-import hook because the pinned
image adds its application package path after `sitecustomize` runs; it observes
only `docreader.parser.web_parser` and removes itself immediately after patching.
Restart `deployment/weknora-docreader` after changing this ConfigMap because Python
imports the hooks only when the process starts.

Create the runtime secret before applying the Kustomize bundle:

```powershell
kubectl -n deer-flow create secret generic weknora-secret `
  --from-literal=DB_PASSWORD='<change-me>' `
  --from-literal=REDIS_PASSWORD='<change-me>' `
  --from-literal=TENANT_AES_KEY='<32-byte-key>' `
  --from-literal=SYSTEM_AES_KEY='<32-byte-key>' `
  --from-literal=LLM_API_KEY='<dashscope-or-compatible-key>' `
  --from-literal=EMBEDDING_API_KEY='<dashscope-or-compatible-key>' `
  --dry-run=client -o yaml | kubectl apply -f -

$neo4jPassword = -join ((48..57) + (65..90) + (97..122) |
  Get-Random -Count 32 | ForEach-Object {[char]$_})
kubectl -n deer-flow create secret generic weknora-neo4j-secret `
  --from-literal=NEO4J_PASSWORD="$neo4jPassword" `
  --dry-run=client -o yaml | kubectl apply -f -
$neo4jPassword = $null

kubectl apply -k k8s/weknora
```

## Knowledge graph

The DeerFlow deployment enables WeKnora's native knowledge-graph capability
without changing WeKnora application source. The Kustomize bundle adds one
Neo4j Community StatefulSet, a persistent data volume, the APOC plugin, and the
documented `ENABLE_GRAPH_RAG`, `NEO4J_ENABLE`, `NEO4J_URI`,
`NEO4J_USERNAME`, and `NEO4J_PASSWORD` application environment variables.
The password exists only in the runtime `weknora-neo4j-secret`.

Neo4j Community is deliberately a single persistent instance. It is suitable
for the local stack and the current enterprise single-instance WeKnora
deployment, but it is not a multi-node HA graph cluster. Before horizontally
scaling this stateful dependency, use a reviewed managed Neo4j service or a
licensed Neo4j cluster and change only the deployment endpoint and Secret.

The UI color and SSO compatibility files under `ui-patches/` are DeerFlow-owned
runtime adaptations mounted into the published WeKnora UI image. They must not
be implemented by editing or forking native WeKnora frontend/backend source.

Configure Gateway with:

```text
WEKNORA_API_URL=http://weknora-app:8080
WEKNORA_SSO_SECRET=<stable-random-secret>
WEKNORA_KNOWLEDGE_BASE_IDS=<knowledge-base-id>
```

When DeerFlow Gateway can reach the WeKnora PostgreSQL database, the SSO bridge
also keeps the DeerFlow default WeKnora models present as platform built-ins
(`is_builtin=true`). Those rows are visible to every WeKnora tenant, including
tenants created after the deployment. The local PVC stack wires these values
from `weknora-secret`. The built-in set contains:

- chat: `qwen-plus`
- chat (Bailian): `deepseek-v4-pro`
- chat (enterprise default, CNOOC Haineng): `DeepSeek-V4-Flash`
- embedding: internal `Qwen3-Embedding-8B` through Gateway
- rerank: internal `Qwen3-Reranker-8B` through Gateway
- vision: `qwen3-vl-plus`
- speech recognition: `qwen3-asr-flash`

The retained Bailian-backed VLM and ASR entries reuse the existing `LLM_API_KEY`;
`EMBEDDING_API_KEY` remains supported for deployments that intentionally use a
different embedding credential. The Haineng entry reads
`DeepSeek_V4_API_KEY` from the existing `deer-flow-env` runtime Secret and is
omitted by Gateway's automatic seed path when that key is absent. Keep
`model.ai.cnooc` in `NO_PROXY`/`no_proxy` so the internal endpoint is never sent
through an outbound Internet proxy.

The versioned enterprise model-defaults Job waits until the
built-in models and credentials exist, then applies
`enterprise-model-feature-defaults.sql` transactionally. It switches empty and
future knowledge bases plus the four built-in agents, then installs database
guards for future objects. Populated knowledge bases remain on their current
embedding model until a controlled re-parse succeeds. New knowledge bases start
with internal `DeepSeek-V4-Flash`, internal `Qwen3-Embedding-8B`,
`qwen3-vl-plus`, and
`qwen3-asr-flash`; new agents start with internal DeepSeek,
internal `Qwen3-Reranker-8B`, image upload, attachment image understanding, and audio upload
enabled. The completed Job is deliberately retained so routine Kustomize
re-applies do not re-normalize user-edited objects. Bump its resource name only
when a revised initializer must run once.

The reusable/local WeKnora model definition keeps `qwen-plus` as the
KnowledgeQA default and sets `WEKNORA_PREFER_CNOOC_DEEPSEEK=false`. A developer
workstation may resolve the enterprise model hostname without having a healthy
route to its upstream inference service. The enterprise runtime overlay sets
that preference back to `true`, and the defaults Job above promotes internal
`DeepSeek-V4-Flash` for enterprise knowledge bases and agents.

The mounted `builtin_agents.yaml` supplies those same ready-to-use defaults to
WeKnora's runtime-generated built-in agents in every personal workspace,
including workspaces created after the one-time Job has completed. The SQL Job
continues to normalize database-backed agent overrides, while the mounted
runtime definition prevents a new ordinary user from seeing the four built-in
agents as unconfigured.

When the DeerFlow runtime Secret contains `TAVILY_API_KEY`, each successful SSO
also ensures that every workspace owned or administered by that user has a
native default provider named `DeerFlow Tavily`. The operation is idempotent;
WeKnora encrypts the provider credential, and the key is never exposed to the
browser or committed to these manifests. Set
`WEKNORA_TAVILY_AUTO_CONFIGURE=false` on Gateway only when a deployment must
manage native search providers separately.

The WeKnora application also uses the existing stable `SYSTEM_AES_KEY` as its
`JWT_SECRET`. This keeps SSO access and refresh tokens valid across application
pod restarts without adding another local secret to maintain.

```text
WEKNORA_DB_HOST=weknora-postgres
WEKNORA_DB_PORT=5432
WEKNORA_DB_USER=postgres
WEKNORA_DB_NAME=WeKnora
WEKNORA_DB_CONNECT_TIMEOUT_SECONDS=3
WEKNORA_DB_PASSWORD=<weknora-secret.DB_PASSWORD>
LLM_MODEL_NAME=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_PROVIDER=aliyun
LLM_API_KEY=<weknora-secret.LLM_API_KEY>
DeepSeek_V4_API_KEY=<deer-flow-env.DeepSeek_V4_API_KEY>
EMBEDDING_MODEL_NAME=text-embedding-v4
EMBEDDING_BASE_URL=http://gateway:8001/api/weknora/bailian/v1
EMBEDDING_PROVIDER=generic
EMBEDDING_API_KEY=<weknora-secret.EMBEDDING_API_KEY>
```

The built-in embedding model calls the internal Gateway adapter at
`http://gateway:8001/api/weknora/bailian/v1/embeddings`. Gateway validates the
same embedding key and forwards the unchanged OpenAI-compatible JSON request
through its managed outbound TLS path. It does not persist document text.

WeKnora 0.7.0 sends speech recognition requests as OpenAI multipart
`/audio/transcriptions`, while Bailian `qwen3-asr-flash` accepts an
OpenAI-compatible chat payload containing `input_audio`. The built-in ASR model
therefore points to
`http://gateway:8001/api/weknora/bailian/v1`. Gateway validates the same Bailian
key, converts the request without persisting the uploaded audio, and calls
Bailian. Keep the bare `gateway` Service name and the reviewed internal
KnowledgeQA host `model.ai.cnooc` in the WeKnora App's `NO_PROXY` and
`no_proxy` values in proxy-enabled clusters. Because both names resolve to
private addresses, keep those exact hostnames in `SSRF_WHITELIST_EXTRA` as
well. Do not disable SSRF validation or allow a broad private CIDR; otherwise
document parsing either fails before Embedding or opens unnecessary access to
unrelated internal endpoints.

For an already-running server database that is missing any of these built-ins,
seed the platform defaults once with `seed-default-models.sql`, then restart or
re-enter through DeerFlow SSO:

```bash
LLM_API_KEY=$(kubectl -n deer-flow get secret weknora-secret -o jsonpath='{.data.LLM_API_KEY}' | base64 -d)
EMBEDDING_API_KEY=$(kubectl -n deer-flow get secret weknora-secret -o jsonpath='{.data.EMBEDDING_API_KEY}' | base64 -d)
CNOOC_DEEPSEEK_API_KEY=$(kubectl -n deer-flow get secret deer-flow-env -o jsonpath='{.data.DeepSeek_V4_API_KEY}' | base64 -d)

kubectl -n deer-flow exec -i weknora-postgres-0 -- env \
  LLM_API_KEY="$LLM_API_KEY" \
  EMBEDDING_API_KEY="$EMBEDDING_API_KEY" \
  CNOOC_DEEPSEEK_API_KEY="$CNOOC_DEEPSEEK_API_KEY" \
  sh -lc 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U postgres -d WeKnora \
    -v summary_model_id="deerflow-qwen-plus" \
    -v embedding_model_id="deerflow-dashscope-embedding" \
    -v builtin_tenant_id="10000" \
    -v llm_model_name="qwen-plus" \
    -v llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1" \
    -v llm_provider="aliyun" \
    -v llm_api_key="$LLM_API_KEY" \
    -v cnooc_deepseek_api_key="$CNOOC_DEEPSEEK_API_KEY" \
    -v embedding_model_name="text-embedding-v4" \
    -v embedding_base_url="http://gateway:8001/api/weknora/bailian/v1" \
    -v embedding_provider="generic" \
    -v embedding_api_key="$EMBEDDING_API_KEY" \
    -v embedding_dimension="1024" \
    -v embedding_truncate_prompt_tokens="0"' < k8s/weknora/seed-default-models.sql
```

DeerFlow user-facing requests do not use a shared WeKnora tenant API key.
Gateway provisions/logs in the matching WeKnora user and forwards requests with
that user's bearer token, so the native WeKnora UI and DeerFlow Knowledge page
show the same personal, joined, team, and public knowledge bases. `WEKNORA_API_KEY`
can still be configured as an optional fallback for background tool calls that
do not have a DeerFlow browser user.

The deployment deliberately uses this workspace policy:

- `WEKNORA_AUTH_DEFAULT_TENANT_MODE=create_personal` creates one personal
  tenant workspace on first SSO registration and gives that user the native
  Owner membership required to maintain their own knowledge bases.
- `WEKNORA_TENANT_SELF_SERVICE_CREATION_ENABLED=false` removes and rejects
  ordinary self-service creation of additional tenant workspaces.
- `WEKNORA_TENANT_MAX_OWNED_PER_USER=1` is a second quota guard for ordinary
  users. WeKnora cross-tenant system administrators are exempt.
- `WEKNORA_TENANT_ENABLE_RBAC=true` keeps native tenant and shared-space
  authorization enforced.

Users collaborate and isolate selected knowledge through Shared Spaces
(WeKnora Organizations), not by creating extra tenants. DeerFlow system
administrators map to WeKnora system administrators and retain full tenant
management.

Local test endpoints:

- WeKnora API: `http://localhost:32080`
- WeKnora UI: `http://localhost:32090`

The `k8s/local-pvc` DeerFlow frontend opens the native UI at
`http://127.0.0.1:32090/platform/knowledge-bases`. It also keeps the same-origin
`http://127.0.0.1:2026/platform/knowledge-bases` proxy as a compatibility
fallback. That proxy uses the stable `weknora-frontend` Kubernetes service DNS
name; do not replace it with a ClusterIP, because ClusterIPs change when the
local Kubernetes cluster is recreated.

If the local Kubernetes environment does not expose NodePorts on the host,
use port-forwarding for the native UI instead:

```powershell
kubectl -n deer-flow port-forward svc/weknora-frontend 8090:80 --address 127.0.0.1
```

Then configure DeerFlow's browser-facing URL as
`WEKNORA_FRONTEND_URL=http://127.0.0.1:8090`.

The bundled WeKnora UI deployment mounts `weknora-ui-patch` over the small
auth API chunk in the upstream UI bundle. That patch makes WeKnora's login-page
auto-setup call DeerFlow's `/api/weknora/sso-session` first, using the current
DeerFlow browser cookie, and falls back to WeKnora's native auto-setup if the
DeerFlow SSO request is unavailable. Keep the host name aligned between
DeerFlow and WeKnora, for example `127.0.0.1:2026` and `127.0.0.1:8090`, so
the DeerFlow cookie is sent.

The SSO bootstrap persists the WeKnora token, user, tenant, membership metadata,
and DeerFlow UI policy. After a successful exchange it also removes the
standalone `weknora_lite_mode` fallback (and its session-only last path), because
Lite mode hides organizations/shared spaces and must not survive an earlier
failed native-auth bootstrap. System administrators keep every native route.
Ordinary users see New Chat, Knowledge Bases, Shared Spaces, and Agents in the
native sidebar. Publish Integrations, system settings, and tenant-management
entries remain hidden, while native RBAC remains the
authorization boundary. Entering the DeerFlow Knowledge page collapses
DeerFlow's own sidebar and the patch expands WeKnora's sidebar once for both
ordinary users and administrators. The native WeKnora sidebar toggle remains
enabled afterwards.
Their user menu hides all management links and logout.
The ordinary-user New Knowledge Base dialog and existing knowledge-base
Settings dialog expose only Basic Information. All native knowledge-base type
and indexing choices remain available on that page, while deployment-managed
defaults populate every hidden model and processing field, including VLM image
handling and ASR audio handling. System administrators retain the native
expandable sidebar and complete knowledge-base editor. On the unified port `2026`,
`/api/v1/auth/me` remains DeerFlow's endpoint, so the patched WeKnora auth chunk
uses `/api/weknora-native/auth/me`. Nginx proxies that exact path to WeKnora's
native `/api/v1/auth/me`, preserving the response shape needed by the user menu,
member management, and API settings. A DeerFlow `system_role` of `admin` maps
to WeKnora `is_system_admin` and `can_access_all_tenants`; normal and
OAuth-provisioned DeerFlow users remain regular WeKnora users.

WeKnora tenant workspaces and organizations are separate concepts. Registration
creates the user's one personal tenant workspace and Owner membership. A system
administrator can create and switch tenant workspaces. Organizations are shared
collaboration spaces with their own admin/editor/viewer membership and
knowledge-base sharing rules.

The same UI patch embeds a branding layer in the mounted `index.html`. It
changes user-visible `WeKnora` product labels to `知识库` at runtime (including
content rendered after navigation), replaces the sidebar image mark with the
text `知识库`, and aligns the native UI's primary blue, active, focus, and
page-background colors with the DeerFlow landing/workspace palette. Internal
API paths, storage keys, container names, and integration identifiers
deliberately remain unchanged.

Do not mount `config.js` or `/etc/nginx/conf.d/default.conf` read-only: the
upstream UI image regenerates both files in `/docker-entrypoint.sh` on every
start. The image builds the proxy configuration from `APP_HOST`, `APP_PORT`,
and `APP_SCHEME`; the patched authentication chunk has the DeerFlow SSO URL as
its fallback. The frontend can therefore restart normally without either
generated-file mount.

For production, replace the local NodePorts with an Ingress or LoadBalancer and
rotate all secrets.
