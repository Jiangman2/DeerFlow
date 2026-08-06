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
attachment flow use the Data Lake terminology. Internal API paths and module
names remain unchanged to avoid an unrelated migration.

Disabling the data lake does not remove files that the user already attached.
Those files have already become ordinary conversation attachments and remain
explicit user input.

## Context Contract

Add `datalake_enabled?: boolean` to the thread run context. An absent value is
treated as `true` for compatibility with existing threads and non-web callers.
The frontend writes the explicit value when the user changes the Data Lake
control. Gateway context allowlisting forwards it to both runtime context and
configurable context through the existing run-context merge path.

This setting is run-scoped. It is not an authorization mechanism and does not
alter administrator tool configuration.

## Backend Enforcement

The AppForge data-lake tool set is identified by the concrete tool names:

- `datalake_smart_qa`
- `datalake_metrics_qa`
- `datalake_detail_qa`

When `datalake_enabled` is false, lead-agent tool assembly removes these tools
before deferred-tool assembly, so they are absent from the model-visible tool
catalog. The same run-context setting is propagated to delegated tasks so
subagents cannot regain the tools.

The AppForge invocation boundary also rejects execution when the runtime
context explicitly disables the data lake. This is defense in depth for stale
tool references, direct invocation, or future assembly paths. Missing context
continues to allow invocation for backward compatibility.

## Error Handling

The existing file-list loading, refresh, download, and partial-failure behavior
is preserved. Disabling the data lake closes the dialog and prevents a new list
request. The backend execution guard returns a bounded, non-sensitive tool
result explaining that data-lake use is disabled; it does not contact AppForge.

## Testing

Frontend tests cover the context-state helper and the rule that the file-list
entry is unavailable when the data lake is disabled. Existing shop-cart parser,
download, and filename tests remain valid because internal names and API
contracts do not change.

Backend tests cover:

- Gateway allowlisting and propagation of `datalake_enabled`.
- Removal of all three AppForge tools when the run disables the data lake.
- Preservation of the tools when the field is absent or true.
- AppForge invocation rejection before configuration or network access.
- Propagation of the setting into delegated-task tool assembly.

Relevant frontend and backend focused tests run first, followed by frontend
checks and backend formatting/lint checks for the touched modules.

## Documentation

Update root and module `AGENTS.md` guidance to describe the conversation-scoped
Data Lake control and backend enforcement. Update `README.md` because the
visible chat input terminology and behavior change.
