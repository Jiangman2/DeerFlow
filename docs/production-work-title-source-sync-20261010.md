# Production Work channel and title source reconciliation — 2026-10-10

This source reconciliation copies exactly the three files from the accepted Gateway
release into the production Git working tree. The running release is
deerflow.local/deer-flow-gateway:20261009-work-channel-16i7k4ob-meta1.
No runtime JSON, environment credentials, PVC backup, ConfigMap or Secret content
belongs in this commit.

## Work channel

The operator can set DEERFLOW_FEISHU_DOMAIN=https://open.w.cnooc.com.cn.
The setting constrains the Feishu-compatible domain during runtime configuration
loading and saving. Without this setting, ordinary Feishu deployments retain
their configured domain.

Preserve the existing NO_PROXY and no_proxy lists and append the exact hosts
open.w.cnooc.com.cn and lark-frontier.w.cnooc.com.cn. Keep both certificate-chain
and hostname verification enabled. These are deployment environment settings,
not hardcoded credentials or a global proxy bypass.

ChannelRuntimeConfigStore uses a stable adjacent lock file, opens it for writing,
and holds a cooperative exclusive flock around fresh reads and updates.
Provider changes are merged into the latest shared file, written to a private
temporary file and atomically replaced. CAS prevents a save based on a stale
provider revision from overwriting a newer revision. The router persists before
restarting its local channel and returns conflict/error responses instead of
rolling back over later configuration.

All configuration writers must follow this locking protocol. Manual edits,
legacy writers, network partitions and power loss are outside the demonstrated
acceptance. A successful disk save does not establish that every live channel
process has reloaded the configuration.

## Private title streaming

TitleMiddleware consumes and closes an explicit asynchronous model stream,
assembles the final title once, and preserves cancellation, existing titles and
fallback behavior. Private title tokens are not streamed into the chat body.

## Verification

The release previously passed native Work regression and isolated synthetic
Gateway lifespan/HTTP acceptance. Five-node NFS acceptance covered 30 concurrent
writes through the candidate API and CAS with one winner and four conflicts.
The unlocked-reader ESTALE failure remains a failed observation and is not
evidence of unlocked read atomicity.

Title acceptance previously included native regression and one bounded synthetic
request to the real enterprise model. The deployed Gateway completed 70/70
per-Pod source/environment/file-preservation acceptance; the operator subsequently
reported that the Work channel connected.

The retained focused regression snapshots use synthetic credentials and files.
Run in an isolated development checkout with project dependencies installed:

    cd backend
    PYTHONPATH=packages/harness:. uv run pytest tests/production_release_20261010 -q

Source preparation compiles the live files with the Pod's Python 3.12 without
importing the application. It does not rerun the full backend suite, contact
Work/model endpoints, inspect chats or load production credentials.
