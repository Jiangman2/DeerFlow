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
  - name: weknora_list_knowledge_bases
    group: knowledge
    use: deerflow.community.weknora.tools:weknora_list_knowledge_bases_tool
    api_url: $WEKNORA_API_URL
    api_key: $WEKNORA_API_KEY

  - name: weknora_get_knowledge_base
    group: knowledge
    use: deerflow.community.weknora.tools:weknora_get_knowledge_base_tool
    api_url: $WEKNORA_API_URL
    api_key: $WEKNORA_API_KEY

  - name: weknora_list_documents
    group: knowledge
    use: deerflow.community.weknora.tools:weknora_list_documents_tool
    api_url: $WEKNORA_API_URL
    api_key: $WEKNORA_API_KEY

  - name: weknora_get_document
    group: knowledge
    use: deerflow.community.weknora.tools:weknora_get_document_tool
    api_url: $WEKNORA_API_URL
    api_key: $WEKNORA_API_KEY

  - name: weknora_list_document_chunks
    group: knowledge
    use: deerflow.community.weknora.tools:weknora_list_document_chunks_tool
    api_url: $WEKNORA_API_URL
    api_key: $WEKNORA_API_KEY

  - name: weknora_read_document
    group: knowledge
    use: deerflow.community.weknora.tools:weknora_read_document_tool
    api_url: $WEKNORA_API_URL
    api_key: $WEKNORA_API_KEY

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

  - name: weknora_list_agents
    group: knowledge
    use: deerflow.community.weknora.tools:weknora_list_agents_tool
    api_url: $WEKNORA_API_URL
    api_key: $WEKNORA_API_KEY

  - name: weknora_invoke_agent
    group: knowledge
    use: deerflow.community.weknora.tools:weknora_invoke_agent_tool
    api_url: $WEKNORA_API_URL
    api_key: $WEKNORA_API_KEY
```

This is DeerFlow's permission-scoped ten-tool public surface over the official
WeKnora v0.7.0 APIs:

- `weknora_list_knowledge_bases` and `weknora_get_knowledge_base` discover and
  inspect the knowledge bases selected for the current DeerFlow run.
- `weknora_list_documents`, `weknora_get_document`,
  `weknora_list_document_chunks`, and `weknora_read_document` discover and read
  parsed document content without exposing storage paths or parser credentials.
- `weknora_search` calls WeKnora `/api/v1/knowledge-search` and returns source
  chunks without LLM summarization. Use it when DeerFlow should reason over the
  retrieved evidence itself.
- `weknora_ask` creates a WeKnora session when `session_id` is omitted, calls
  `/api/v1/knowledge-chat/{session_id}`, and returns WeKnora's RAG answer with
  references.
- `weknora_list_agents` discovers the current user's enabled built-in and custom
  WeKnora agents. `weknora_invoke_agent` calls
  `/api/v1/agent-chat/{session_id}` so the selected WeKnora agent can use its
  native tools (for example smart reasoning, data analysis, or Wiki research)
  under WeKnora's own authorization boundary.

The names above are DeerFlow wrappers; their underlying retrieval, chat,
document, and agent endpoints are official WeKnora capabilities. DeerFlow does
not register each internal WeKnora agent tool as an unrestricted local tool.
That distinction is intentional: agent tool calls remain inside WeKnora, raw
system prompts are omitted, and human-approval events are returned as
`pending_actions` rather than auto-approved. Wiki repair and other mutation-
capable agents should be invoked only after an explicit user request.

Search and ask accept per-call `knowledge_base_id`, `knowledge_base_ids`, and
`knowledge_ids`. Agent invocation requires a real ID returned by
`weknora_list_agents`; callers must not guess built-in IDs.

### What triggers each tool

Tool selection is model-driven from the user's intent and the selected knowledge
scope; it is not a hard-coded Chinese-keyword switch. The hidden scope reminder
describes the following division of responsibility, and the agent may chain
several tools when one request needs discovery plus evidence:

| DeerFlow wrapper | What it returns | Typical user intent |
| --- | --- | --- |
| `weknora_list_knowledge_bases` | The knowledge bases available inside the current run scope | “我刚才选了哪些库？” “哪个库还在处理中？” |
| `weknora_get_knowledge_base` | Metadata and status for one selected knowledge base | “这个制度库是什么类型？” “这个库的说明和状态是什么？” |
| `weknora_list_documents` | Documents visible inside the exact selected KB/file/tag scope | “这个库有哪些 PDF？” “列出标题含井控的文件。” |
| `weknora_get_document` | Metadata and parse state for one selected document | “这份文件什么时候上传的？” “它为什么解析失败？” |
| `weknora_list_document_chunks` | Parsed chunks for one selected document | “给我第 20 段附近的解析原文。” “检查这页 OCR 分块。” |
| `weknora_read_document` | Bounded, ordered parsed content for one selected document | “通读这份报告并总结。” “按章节梳理这份制度。” |
| `weknora_search` | Ranked source chunks, citations, and authenticated images; no upstream answer synthesis | “找出支持这个结论的原文。” “比较几份文件中的规定并给出处。” |
| `weknora_ask` | WeKnora's RAG answer plus references inside the selected scope | “根据这些材料直接回答这个问题。” “按知识库内容给我一个带依据的结论。” |
| `weknora_list_agents` | Enabled built-in/custom WeKnora agents and their real IDs | “有哪些可用的知识库智能体？” “有没有适合数据分析的智能体？” |
| `weknora_invoke_agent` | Native WeKnora agent-chat result, references, and any pending approval events | “让数据分析智能体分析这些材料。” “用深度推理/Wiki 研究智能体完成这项工作。” |

Common safe chains include `list_knowledge_bases -> list_documents ->
read_document`, `search -> read_document`, and `list_agents -> invoke_agent`.
`weknora_search` is preferred when DeerFlow itself should compare or synthesize
the retrieved evidence; `weknora_ask` is preferred when WeKnora should perform
the RAG answer step. If an agent ID is not already present in the current turn,
the model must call `weknora_list_agents` before `weknora_invoke_agent`.

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

The knowledge-base list refreshes the native `/auth/me` membership payload before
enumerating resources, then queries every tenant with `X-Tenant-ID`. This keeps
newly created and newly joined spaces visible even while the login token itself is
cached. `creator_id` determines whether a row is owned; non-owned accessible rows
are joined resources; organization shares and resources with active shares are
also included in the team/public dimension. Those dimensions may overlap (for
example, a user-owned knowledge base shared to a team). Document list, upload, and
manual-document requests forward the access tenant separately from the resource's
owner tenant so read-only and cross-space permissions remain native to WeKnora.

The chat composer includes a native-style knowledge-range selector. It can scope
one run to complete knowledge bases, individual files, or tags. Selected values
are sent as `weknora_knowledge_base_ids`, `weknora_knowledge_ids`, grouped
`weknora_tag_scopes`, and mention-shaped `weknora_scope_items` (including each
file/tag's parent knowledge-base identity). Choosing a complete knowledge base
removes its child file/tag selections; choosing a child scope removes that
complete-KB selection, so the effective scope is unambiguous.

`WeKnoraScopeMiddleware` injects the same exact scope into the lead and delegated
agents. The run worker binds it as immutable execution context, so model-supplied
arguments cannot widen the browser selection. Bare tag IDs without their parent
knowledge base are rejected during normalization. Without an explicit KB/file/tag
selection, all ten tools are removed from lead and delegated agents.

The WeKnora native UI can also be opened directly at the local WeKnora frontend
port, such as `http://127.0.0.1:32090` for the bundled Kubernetes NodePort
deployment or `http://127.0.0.1:8090` when using a manual port-forward. The
DeerFlow-patched WeKnora frontend
calls `/api/weknora/sso-session` on the DeerFlow Gateway, uses the current
DeerFlow cookie, and hydrates the native WeKnora session automatically. Use the
same host name for both apps, for example `127.0.0.1` for DeerFlow and WeKnora,
so browser cookies are sent correctly.
