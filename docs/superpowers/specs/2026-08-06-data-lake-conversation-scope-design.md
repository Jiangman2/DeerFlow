# Data Lake Conversation Scope Design

## Goal

Rename the existing chat input "shop cart" experience to "data lake" and add
a per-conversation opt-out. When the user selects "Do not use data lake", the
existing shop-cart file list is not offered and the AppForge-backed data-lake
tools cannot be used for that run.

## User Experience

The chat input keeps a visible Data Lake control so the user can change the
setting at any time. Its menu has two states:

- Data lake enabled: the menu offers the existing data-lake file list and a
  "Do not use data lake" action.
- Data lake disabled: the file-list action is absent and the menu offers an
  action to enable data-lake use again.

All user-visible Chinese and English strings in the existing shop-cart
attachment flow use the Data Lake terminology, including the attachment
filename fallback and the workspace about text. Internal API paths, source
module names, request types, and server route names remain unchanged to avoid
an unrelated API migration.

Disabling the data lake does not remove files that the user already attached.
Those files have already become ordinary conversation attachments and remain
explicit user input.

## Context Contract

Add `datalake_enabled?: boolean` to the thread run context. The setting is
conversation-scoped in the frontend: persist it under a key containing the
current `thread_id`, default new and legacy threads to `true`, and write the
explicit value when the user changes the Data Lake control. Re-enabling writes
`true`; switching, reloading, regenerating, and branching a conversation must
resolve the value from that conversation's state. Every normal, custom-agent,
regenerate, and branch run sends the resolved boolean explicitly.

Gateway accepts this field only from the validated `body.context` mapping. It
must be a JSON boolean; malformed values and conflicting low-level config
values are rejected rather than coerced. `body.context` is authoritative over
`body.config`, and the field is copied to `config["context"]` only. It is
runtime-only and must never enter checkpoint-persisted `configurable` state.
Absent values from old or non-web callers resolve to `true`.

This setting controls tool availability for a run; it is not an authorization
mechanism and does not alter administrator tool configuration.

## Backend Enforcement

The AppForge data-lake tool set is identified by the concrete tool names:

- `datalake_smart_qa`
- `datalake_metrics_qa`
- `datalake_detail_qa`

When `datalake_enabled` is false, lead-agent tool assembly removes these tools
before deferred-tool assembly, so they are absent from the model-visible tool
catalog. `task_tool` copies the effective flag into the `SubagentExecutor`
constructor, and the executor writes it into the child runtime context before
creating the child agent. Child assembly applies the same filter before
deferred-tool assembly, so a delegated task cannot regain the tools.

The AppForge invocation boundary checks the effective runtime context as its
first operation, before `_tool_extra`, credential, app-id, or network access.
When the value is explicitly `false`, it returns a stable bounded JSON result
with `code: "datalake_disabled"` and `retryable: false`; it never contacts
AppForge. This is defense in depth for stale tool references, direct
invocation, or future assembly paths. Missing context continues to allow
invocation for backward compatibility, and `runtime=None` is treated as
missing context.

## Error Handling

The existing file-list loading, refresh, download, and partial-failure behavior
is preserved. Disabling the data lake closes the dialog and prevents a new list
request. The backend execution guard returns a bounded, non-sensitive tool
result explaining that data-lake use is disabled; it does not contact AppForge.

## Testing

Frontend tests cover the per-thread context-state helper, request context on
normal and custom-agent sends, regenerate/branch resolution, re-enable, and
thread reload/switch behavior. Component tests verify that disabling closes an
open dialog, removes the file-list entry, prevents new list requests, and
ignores stale in-flight list responses. Filename and about-text expectations
change to Data Lake terminology; internal shop-cart API parser and download
contracts remain unchanged.

Backend tests cover:

- Gateway allowlisting and propagation of `datalake_enabled`.
- Strict boolean validation, `body.context` precedence over low-level config,
  runtime-only storage, and absent/legacy default behavior.
- Removal of all three AppForge tools when the run disables the data lake.
- Preservation of the tools when the field is absent or true.
- AppForge invocation rejection before configuration or network access,
  including runtime context, configurable-only, and `runtime=None` shapes.
- Propagation through `task_tool` and `SubagentExecutor`, child catalog
  filtering before deferred assembly, and stale direct tool references.

Relevant frontend and backend focused tests run first, followed by frontend
checks and backend formatting/lint checks for the touched modules.

## Documentation

Update root and module `AGENTS.md` guidance to describe the conversation-scoped
Data Lake control and backend enforcement. Update `README.md` because the
visible chat input terminology and behavior change.
