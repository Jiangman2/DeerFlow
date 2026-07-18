# WeKnora Integration

WeKnora is integrated as an external knowledge platform. DeerFlow does not store
or index WeKnora documents directly; WeKnora owns ingestion, parsing, retrieval,
permissions, and RAG. DeerFlow maps the current DeerFlow account to a matching
WeKnora account and calls WeKnora with that user's bearer token, so knowledge
base lists, uploads, shares, public/team knowledge bases, and agent retrieval
all stay scoped to the logged-in user.

## Minimal setup

1. Deploy WeKnora separately. For the local Kubernetes stack, use
   `kubectl apply -k k8s/weknora`.
2. Configure DeerFlow:

```bash
WEKNORA_API_URL=http://localhost:8080
WEKNORA_SSO_SECRET=replace-with-a-stable-random-secret
WEKNORA_KNOWLEDGE_BASE_IDS=kb-00000001,kb-00000002
```

When Gateway runs inside Docker or Kubernetes, `WEKNORA_API_URL` must be a URL
that the Gateway container can reach. In the bundled local Kubernetes
deployment, use the in-cluster service URL:

```bash
WEKNORA_API_URL=http://weknora-app:8080
```

`WEKNORA_SSO_SECRET` must be stable. Changing it changes the deterministic
password DeerFlow uses for DeerFlow-provisioned WeKnora users, which can make
existing linked accounts fail to log in. If it is omitted, DeerFlow falls back
to `AUTH_JWT_SECRET`, then legacy API/internal secrets.

For Docker Desktop local Kubernetes where WeKnora still runs on the host, use
`http://host.docker.internal:8080`. For production, run WeKnora as a stable
Service plus Ingress/LoadBalancer instead of relying on a local Compose process.

## Tools

Enable the tools in `config.yaml`:

```yaml
tool_groups:
  - name: web
  - name: knowledge

tools:
  - name: weknora_search
    group: knowledge
    use: deerflow.community.weknora.tools:weknora_search_tool
    api_url: $WEKNORA_API_URL
    api_key: $WEKNORA_API_KEY
    knowledge_base_ids: $WEKNORA_KNOWLEDGE_BASE_IDS
    max_results: 8

  - name: weknora_ask
    group: knowledge
    use: deerflow.community.weknora.tools:weknora_ask_tool
    api_url: $WEKNORA_API_URL
    api_key: $WEKNORA_API_KEY
    knowledge_base_ids: $WEKNORA_KNOWLEDGE_BASE_IDS
```

`weknora_search` calls WeKnora `/api/v1/knowledge-search` and returns source
chunks without LLM summarization. Use it when DeerFlow should reason over the
retrieved evidence itself.

`weknora_ask` creates a WeKnora session when `session_id` is omitted, calls
WeKnora `/api/v1/knowledge-chat/{session_id}`, and returns WeKnora's RAG answer
with references. Use it when WeKnora should perform the RAG answering step.

Both tools also accept per-call `knowledge_base_id`, `knowledge_base_ids`, and
`knowledge_ids`.

When a chat run is started from the DeerFlow UI with selected WeKnora knowledge
bases, Gateway injects the current user's WeKnora bearer token into the run
context. The WeKnora tools prefer that user token over `WEKNORA_API_KEY`.
`WEKNORA_API_KEY` remains a fallback for non-browser/background usage where no
DeerFlow user is available.

## DeerFlow UI

The workspace exposes WeKnora through `/workspace/knowledge`. The page calls the
Gateway proxy under `/api/weknora`; Gateway provisions/logs in the matching
WeKnora user and forwards requests with that user's bearer token. The browser
never receives a global WeKnora API key. The supported management flows are:

- list and create WeKnora knowledge bases;
- upload local files into a selected knowledge base;
- create manual Markdown knowledge and publish it for processing;
- list document parse status and refresh processing results.

The identity bridge is idempotent: it attempts registration with a stable,
service-managed credential and then logs in, so the first visit creates the
matching WeKnora account while later visits reuse it. After login, DeerFlow
mirrors `system_role=admin` to WeKnora's system-admin and all-tenant flags;
regular DeerFlow users remain regular WeKnora users and keep the owner/member
role returned for their own tenant.

Gateway proxy calls reuse each linked WeKnora session for a short configurable
window (`WEKNORA_SESSION_CACHE_TTL_S`, 900 seconds by default), avoiding a
register/login round trip for every list or upload request. Knowledge-base list
rows also keep WeKnora's aggregate document/chunk/processing counts when those
fields are already present instead of issuing per-document count requests.

The chat composer includes a "knowledge base" scope selector. Selected knowledge
base IDs are sent with the LangGraph run context as
`weknora_knowledge_base_ids`, plus display names as `weknora_knowledge_bases`.
`WeKnoraScopeMiddleware` injects a hidden system reminder telling the agent to
pass those IDs to `weknora_search` or `weknora_ask` when the task needs local or
enterprise knowledge.

The WeKnora native UI can also be opened directly at the local WeKnora frontend
port, such as `http://127.0.0.1:32090` for the bundled Kubernetes NodePort
deployment or `http://127.0.0.1:8090` when using a manual port-forward. The
DeerFlow-patched WeKnora frontend
calls `/api/weknora/sso-session` on the DeerFlow Gateway, uses the current
DeerFlow cookie, and hydrates the native WeKnora session automatically. Use the
same host name for both apps, for example `127.0.0.1` for DeerFlow and WeKnora,
so browser cookies are sent correctly.
