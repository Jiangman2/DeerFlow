# Data Lake User Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the chat shop-cart flow to Data Lake, add a user-global opt-out, and prevent AppForge Data Lake tools when disabled.

**Architecture:** Persist `datalake_enabled` in the frontend's shared local settings and send the resolved boolean on every chat run. Gateway accepts it only as validated runtime context; lead and delegated agents filter the three AppForge tools before deferred assembly, while `tools.py` rejects stale direct calls before reading configuration or making network requests.

**Tech Stack:** Next.js/React/TypeScript, Rstest, FastAPI/Python, LangChain tools, pytest.

---

### Task 1: Define user-scoped frontend state

**Files:**
- Modify: `frontend/src/core/threads/types.ts`
- Modify: `frontend/src/core/settings/local.ts`
- Modify: `frontend/src/core/settings/store.ts`
- Modify: `frontend/src/core/settings/hooks.ts`
- Test: `frontend/tests/unit/core/settings/data-lake.test.ts`

- [ ] **Step 1: Write failing state tests** for default `true`, shared-setting persistence, per-thread model overrides, and re-enable.
- [ ] **Step 2: Run `cd frontend && pnpm test tests/unit/core/settings/data-lake.test.ts` and verify the new helper/state API fails.**
- [ ] **Step 3: Add `datalake_enabled?: boolean` to `AgentThreadContext` and the shared `LocalSettings.context`, default it to `true`, and let `useThreadSettings` inherit that user-global value while preserving per-thread model overrides.**
- [ ] **Step 4: Run the focused test and verify it passes.**
- [ ] **Step 5: Commit `feat: persist data lake scope globally`.**

### Task 2: Add Gateway runtime-context validation

**Files:**
- Modify: `backend/app/gateway/services.py`
- Test: `backend/tests/test_gateway_services.py`

- [ ] **Step 1: Write failing tests** for accepted booleans, absent default behavior, runtime-only placement, low-level config rejection, non-boolean rejection, and stable HTTP 400 detail `{code: "invalid_datalake_context", retryable: false}`.
- [ ] **Step 2: Run the focused pytest tests and verify failure before implementation.**
- [ ] **Step 3: Add `datalake_enabled` to the runtime-only allowlist, validate `body.context` strictly, reject the same key in `config.context`/`config.configurable`, and copy only the validated value to `config["context"]` without `setdefault` persistence.**
- [ ] **Step 4: Run the focused Gateway tests and the existing context-merge tests.**
- [ ] **Step 5: Commit `feat: validate data lake run context`.**

### Task 3: Enforce AppForge runtime guard and lead-agent filtering

**Files:**
- Modify: `backend/packages/harness/deerflow/community/appforge/tools.py`
- Modify: `backend/packages/harness/deerflow/agents/lead_agent/agent.py`
- Modify: `backend/packages/harness/deerflow/tools/tools.py` (shared named-tool filter)
- Test: `backend/tests/test_appforge_tools.py`
- Test: `backend/tests/test_lead_agent_model_resolution.py`

- [ ] **Step 1: Write failing tests** proving false context removes all three named tools, absent/true preserves them, and `_invoke_tool` returns `datalake_disabled` before `_tool_extra`, credentials, or HTTP client access.
- [ ] **Step 2: Run the focused tests and verify the expected failures.**
- [ ] **Step 3: Implement a shared `DATALAKE_TOOL_NAMES` filter, apply it to lead-agent raw tools before deferred assembly, and add the first-line AppForge guard returning bounded JSON `{code, retryable: false}`.**
- [ ] **Step 4: Run focused backend tests and confirm all existing AppForge parsing/network tests remain green.**
- [ ] **Step 5: Commit `feat: block data lake tools for disabled runs`.**

### Task 4: Propagate the flag through delegated tasks

**Files:**
- Modify: `backend/packages/harness/deerflow/tools/builtins/task_tool.py`
- Modify: `backend/packages/harness/deerflow/subagents/executor.py`
- Modify: `backend/packages/harness/deerflow/agents/lead_agent/agent.py`
- Test: `backend/tests/test_task_tool_core_logic.py`

- [ ] **Step 1: Write failing tests** for false parent context reaching `SubagentExecutor`, child runtime context, child tool catalog filtering before deferred assembly, and stale direct tool calls.
- [ ] **Step 2: Run the focused delegation tests and verify failure.**
- [ ] **Step 3: Add an explicit `datalake_enabled` constructor field/context copy and apply the shared named-tool filter to child tools before the child agent is created.**
- [ ] **Step 4: Run delegation, AppForge, and lead-agent tests together.**
- [ ] **Step 5: Commit `feat: propagate data lake scope to subagents`.**

### Task 5: Replace user-visible shop-cart terminology and wire the Data Lake control

**Files:**
- Modify: `frontend/src/components/workspace/input-box.tsx`
- Modify: `frontend/src/components/workspace/shop-cart-attachments/shop-cart-attachment-button.tsx`
- Modify: `frontend/src/core/i18n/locales/zh-CN.ts`
- Modify: `frontend/src/core/i18n/locales/en-US.ts`
- Modify: `frontend/src/core/shop-cart/filenames.ts`
- Modify: `frontend/src/components/workspace/settings/about-content.ts`
- Test: `frontend/tests/unit/core/settings/data-lake.test.ts`
- Test: focused component tests under `frontend/tests/unit/components/workspace/`

- [ ] **Step 1: Write failing component/helper tests** for the enabled menu, disabled menu with no file-list action, re-enable, dialog close, stale list response suppression, and context callback payload.
- [ ] **Step 2: Run the focused frontend tests and verify failure.**
- [ ] **Step 3: Replace `ShopCartAttachmentButton` with a Data Lake control that owns the existing dialog only when enabled, closes/invalidates in-flight loads when disabled, and calls `onContextChange({ ...context, datalake_enabled })`; rename all visible strings and filename fallback while keeping internal routes/modules.**
- [ ] **Step 4: Run focused component/i18n/filename tests and verify pass.**
- [ ] **Step 5: Commit `feat: add data lake opt-out control`.**

### Task 6: Verify every run path and documentation

**Files:**
- Modify: `frontend/src/app/workspace/chats/[thread_id]/page.tsx`
- Modify: `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `backend/AGENTS.md`
- Modify: `frontend/AGENTS.md`
- Test: relevant frontend chat/e2e request tests and backend run-context tests

- [ ] **Step 1: Add failing integration assertions** that normal/custom-agent send, regenerate, and branch requests include the user-global boolean.
- [ ] **Step 2: Run focused frontend and backend tests and verify failures.**
- [ ] **Step 3: Wire the user-global setting into both chat pages and update user/developer docs with Data Lake behavior and tool boundary.**
- [ ] **Step 4: Run `cd frontend && pnpm check`, focused `pnpm test`, `cd backend && pytest -q` for touched suites, `ruff format --check`, and `ruff check`.**
- [ ] **Step 5: Review `git diff`, confirm no internal shop-cart route/API names changed, and commit `docs: document data lake user scope`.**

## Verification Checklist

- [ ] Frontend focused tests pass.
- [ ] Backend focused tests pass.
- [ ] Frontend check/type/lint passes.
- [ ] Backend format/lint passes.
- [ ] Existing shop-cart API contracts remain unchanged.
- [ ] Disabled runs expose no AppForge Data Lake tool and never contact AppForge.
