# Isolated PPT Master Agent Host

Explicit `/ppt-master` tasks use the vendored upstream `ppt-master` v6.6.0
package through a real external Agent CLI. DeerFlow does not reinterpret that
Skill as a second LangChain Agent.

## Runtime boundary

There are two deliberately different processes:

1. `ppt-master-runner` is an independent Deployment exposed only by the
   `ppt-master-runner` ClusterIP Service on port `8003`. It is only a
   LangGraph-compatible streaming/checkpoint transport. Its graph has one relay
   node and contains no model factory, `create_agent`, Skill middleware, loop
   guard, summarizer, or presentation workflow schema.
2. `ppt-master-agent-host` on port `8788` is a second container in each
   provisioned Sandbox Pod. It runs the real Codex Agent CLI beside the fixed
   Office/Chromium/font/Python/Node dependencies and the Skill's Confirm UI.

The Provisioner adds the host only in Kubernetes deployments where
`PPT_MASTER_AGENT_HOST_ENABLED=true`. Its Service must be `ClusterIP`: the
control API is authenticated with the existing `SANDBOX_API_KEY` and must never
be exposed as a NodePort. Both containers in the Sandbox Pod share network and
the thread's user-data/Skills volumes, so a Confirm UI bound to port `5050`
remains reachable through the existing authenticated Live Preview proxy.

The transport runner never shares the ordinary Gateway Pod. Its init phase
resolves and connects to PostgreSQL repeatedly and requires three consecutive
successful `SELECT 1` samples within a bounded deadline. A transient service-DNS
or database readiness failure can restart or delay the runner without changing
the readiness or endpoint membership of the ordinary Gateway. Nginx and Gateway
both reach the transport by the stable `ppt-master-runner:8003` service name.

## Upstream Skill and writable state

The public Skills volume stays read-only. The release image carries the locked
upstream tree at `/opt/ppt-master-release-skill`; this immutable image content,
not the mutable shared Skills PVC, is the source of truth used by Agent Host.
Before a turn, the host computes that release tree's package hash and atomically
refreshes an exact writable copy at:

`/mnt/user-data/workspace/.ppt-master-runtime`

It does not merge old local template edits into that copy. The runtime copy is
linked into the Agent's normal `$CODEX_HOME/skills/ppt-master` location, and the
upstream attribution guard must pass before Codex starts. Agent task state,
public JSONL command/file/message receipts, and the resumable Codex session
pointer live under:

`/mnt/user-data/workspace/.ppt-master-agent`

These paths are on user-data, not container ephemeral storage. A Pod restart or
new DeerFlow run can therefore resume the same official Agent session as long as
the storage backend remains available.

## Thin protocol bridge

DeerFlow retains only these duties:

1. authenticate the user, provision the thread Pod, and start/resume/cancel an
   Agent turn;
2. stream genuine Codex command, file-change, tool, and visible Agent-message
   receipts through the existing `task_started`, `task_running`, and terminal
   task events so the execution timeline is durable after refresh;
3. proxy the Skill-owned Confirm UI without fabricating its confirmation files;
4. persist final files from `/mnt/user-data/outputs` and create a paired
   `.preview.pdf` with Sandbox LibreOffice when needed.

Raw model reasoning events are neither displayed nor persisted. The bridge does
not decide route, stage order, template semantics, schema validity, retry
policy, or phase progression. Those remain owned by the v6.6.0 Skill documents
and scripts.

The vendored tree is locked to the upstream v6.6.0 tag, commit, and Skill tree
recorded in `docker/sandbox-ppt-master/PPT_MASTER_UPSTREAM.env`. The maintained
enterprise delta inside that tree is limited to the CNOOC Deck registration and
safe explicit-argv process launching with `shell=False`. v6.6.0's runtime
authorities remain upstream-owned: Image-to-PPTX and Beautify choose their
documented child runtime, and the removed v4.5 native-enhance/template-fill
routes are not reintroduced by DeerFlow.

The production AIO base currently uses Python 3.10. Agent Host and the runtime
capability probe therefore use `tomli` as the compatibility provider for the
Python 3.11 `tomllib` API; the dependency is installed in the release image and
is never fetched during a conversation.

The Gateway API and `ppt-master-runner` are separate Pods and therefore do not
share their in-memory active/warm Sandbox maps or readiness failure domain.
During the complete external
Agent turn the runner holds a shared lease file on the Gateway user-data volume.
Idle reaping, replica eviction, unhealthy cleanup, explicit destruction, and
process shutdown may destroy that Sandbox only after obtaining a non-blocking
exclusive lease; while execution is active they defer deletion and restore the
warm entry to tracking. The lease is released automatically when the runner
process exits, so it cannot become a permanent stale lock.

The bridge does enforce one deterministic delivery boundary around those
upstream semantics. Every Agent turn receives the original Slash request again
and is required to re-read the current upstream authorities before making a
project mutation. Owning validators and exporters must run as exact commands;
terminal `head`/`tail`/`grep` pipes, `|| true`, and other shell composition that
can hide the real exit status are rejected. A terminal Agent answer is accepted
only after the upstream `pptx_delivery_check.py` succeeds and an independent
OOXML inspection confirms the capabilities explicitly requested by the user,
including slide count, notes, transitions, timed advance, object animations,
native chart parts, template structure, images, and audio when applicable.
Visual imitation of a chart is not a native chart, and a page transition is not
an object animation.

The host snapshots output files before each turn and publishes only a new or
changed PPTX from that turn. When the official checker or package inspection
finds a mismatch, the host resumes the same persisted Agent session and project
with the concrete failure instead of returning the model's completion claim.
This repair is bounded to three attempts. A paired preview is regenerated when
it is missing or older than the accepted PPTX. These checks do not recreate the
Skill workflow; they make its final, user-visible contract deterministic.

The frontend renders the genuine Agent receipts as the normal durable task
timeline. Task state is keyed by the dynamic thread id because Next.js preserves
the surrounding layout during client-side navigation; a new chat must never
inherit the previous thread's PPT workflow card. On page reload, it restores the
artifact channel explicitly from the thread state snapshot when the tab-local
stream has not hydrated that channel yet. Preview companions remain hidden from
the file selector, while the selected PPTX opens the authenticated five-page PNG
gallery backed by its persisted PDF.

## Agent provider requirement

The host requires one real Codex authentication/provider path supplied through
Kubernetes Secret data: Codex auth or a selected custom provider whose
`env_key` is present and non-empty. `CODEX_CONFIG_TOML` configures a provider;
it is not itself a credential. Custom Codex providers use the Responses wire
protocol. An existing OpenAI-compatible Chat Completions URL is not sufficient
merely because DeerFlow can use it as a LangChain chat model.

The checked-in Kubernetes configuration deliberately uses two provider paths:

- Local Docker Desktop Kubernetes uses Alibaba Cloud Model Studio's native
  Responses endpoint with `qwen3.7-max`. It uses the existing
  `DASHSCOPE_API_KEY`, the same account key used by other approved Qwen models;
  no second Qwen credential is required. The local model catalog declares the
  documented one-million-token context window and a bounded 850,000-token
  automatic-compaction threshold.
- Enterprise Kubernetes uses `DeepSeek-V4-Flash` through Gateway's authenticated
  `/api/codex/deepseek/v1/responses` bridge. The Sandbox Agent container receives
  only `PPT_MASTER_RESPONSES_RELAY_KEY`; the upstream `DeepSeek_V4_API_KEY`
  remains in Gateway. The bridge translates Codex Responses items and tools to
  the enterprise streamed Chat Completions contract, maps thinking controls,
  discards raw reasoning text, and converts the streamed result back to
  Responses events. The internal model route stays direct with
  `trust_env=false`; normal CA and hostname verification are never replaced by
  `verify=false`.

The enterprise Provisioner sets `PPT_MASTER_AGENT_CODEX_CONFIG_TOML` and
`PPT_MASTER_AGENT_CODEX_MODEL_CATALOG_JSON` only on the Agent Host container as
`CODEX_CONFIG_TOML` and `CODEX_MODEL_CATALOG_JSON`. The ordinary Sandbox keeps
its inherited Codex configuration. When
`PPT_MASTER_ENTERPRISE_RELAY_REQUIRED=true`, creation fails closed if either
Agent-only profile value is absent. Do not replace the Provisioner's shared
`CODEX_*` settings merely to make the PPT canary pass.

`PPT_MASTER_RESPONSES_RELAY_KEY` must be generated independently from every
upstream model credential and be identical in Gateway and the provisioned Agent
host. The ordinary AIO Sandbox container must not receive it.

Enterprise presentation media uses a separate least-privilege capability set
inside the co-scheduled Agent host. `DASHSCOPE_API_KEY` enables the official
`qwen-image-2.0-pro` image backend and optional `qwen3-tts-flash` narration.
Set `QWEN_TTS_BASE_URL=https://dashscope.aliyuncs.com` explicitly so mainland
credentials do not fall through to the upstream script's international default.
`PEXELS_API_KEY` and `PIXABAY_API_KEY` remain optional because the official
search tool retains Openverse and Wikimedia zero-key fallbacks. Release
preflight must execute one real Agent/Codex/Responses turn, one generated image,
one narration sample, one licensed-image search, and the local Office/browser/
audio-video conversion toolchain from the target immutable Agent Host image.
Enterprise rollout also needs a real two-container Pod: the pinned, previously
validated AIO image must answer its authenticated command API and execute a
short command, while the Agent Host image must answer its own health endpoint.
An Agent-Host-only probe cannot establish AIO Sandbox readiness.

Missing credentials/configuration fail before a billable Agent turn starts.
The bridge never silently falls back to DeerFlow's lead Agent or to the removed
LangChain-based PPT runner.

The local acceptance check uses Codex CLI with a real Shell tool round-trip;
an HTTP 200 from `/responses` alone is not sufficient. The enterprise path must
repeat that same tool round-trip against the real internal model after images
and Secrets are installed on the cluster, because Docker Desktop cannot reach
the enterprise-only endpoint.

## Multi-node Kubernetes

The Agent host is co-scheduled with its AIO Sandbox container, so command and
Confirm UI traffic never depends on node-local addresses. The per-thread
ClusterIP Service is stable across nodes.

To schedule these Pods on multiple workers, user-data must be RWX (or an
equivalent shared filesystem) because it contains uploads, projects, Agent
sessions, events, outputs, and preview files. Skills must be available through
ROX/RWX storage or baked immutably into the image. Every eligible worker must
preload both the proven AIO Sandbox image and the separate PPT Agent Host image
when `imagePullPolicy: Never` is used. Enterprise Provisioner configuration
must set a distinct `PPT_MASTER_AGENT_HOST_IMAGE`; the relay-required profile
rejects a missing or shared image. A release preflight must start a Pod with
both actual containers and verify AIO port 8080 command execution and Agent
Host port 8788 health before changing production workloads. The checked-in
enterprise local-PV profile remains intentionally single-node/single-Gateway
until those storage and distributed run-ownership
requirements are met.

## Migration

Only a latest human message beginning with `/ppt-master` creates the isolated
runtime profile. Later continuation messages use the same port-8003 transport
and resume the session pointer persisted by the Sandbox Agent host. Ordinary
Skills and legacy presentation threads remain on the main lead Agent during
migration; no new PPT workflow semantics belong in that legacy middleware path.
