import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export interface ConfirmationState {
  protocol: "ppt-confirmation-v1";
  thread_id: string;
  task_id: string;
  admitted: boolean;
  auto_enabled: boolean;
  cancelled: boolean;
  run_active: boolean;
  can_open: boolean;
  gate: {
    state: string;
    stage: string;
    due_at: number | null;
    auto_block_reason: string | null;
  } | null;
}

function path(threadId: string, taskId: string) {
  if (![threadId, taskId].every((id) => /^[A-Za-z0-9_-]{1,128}$/.test(id)))
    throw new Error("invalid_confirmation_identity");
  return `${getBackendBaseURL()}/api/threads/${threadId}/ppt-confirmations/${taskId}`;
}

async function boundedJSON(response: Response): Promise<unknown> {
  if (
    !response.ok ||
    !response.body ||
    !response.headers.get("content-type")?.includes("application/json")
  )
    throw new Error("confirmation_unavailable");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = "",
    bytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 16384) throw new Error("confirmation_response_limit");
      text += decoder.decode(value, { stream: true });
    }
    return JSON.parse(text + decoder.decode()) as unknown;
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

export async function fetchConfirmationState(
  threadId: string,
  taskId: string,
  signal: AbortSignal,
): Promise<ConfirmationState> {
  const value = await boundedJSON(
    await fetch(path(threadId, taskId) + "/state", {
      signal,
      cache: "no-store",
      redirect: "error",
    }),
  );
  if (!value || typeof value !== "object")
    throw new Error("invalid_confirmation_state");
  const row = value as Record<string, unknown>;
  if (
    row.protocol !== "ppt-confirmation-v1" ||
    row.thread_id !== threadId ||
    row.task_id !== taskId ||
    !["admitted", "auto_enabled", "cancelled", "run_active", "can_open"].every(
      (key) => typeof row[key] === "boolean",
    )
  )
    throw new Error("invalid_confirmation_state");
  if (row.gate !== null) {
    if (!row.gate || typeof row.gate !== "object")
      throw new Error("invalid_confirmation_gate");
    const gate = row.gate as Record<string, unknown>;
    if (
      typeof gate.state !== "string" ||
      gate.state.length > 64 ||
      typeof gate.stage !== "string" ||
      !["stage1", "stage2"].includes(gate.stage) ||
      !(gate.due_at === null || Number.isSafeInteger(gate.due_at)) ||
      !(
        gate.auto_block_reason === null ||
        typeof gate.auto_block_reason === "string"
      )
    )
      throw new Error("invalid_confirmation_gate");
  }
  return row as unknown as ConfirmationState;
}

export function stableConfirmationEntry(threadId: string, taskId: string) {
  path(threadId, taskId); // Shared strict identities, not arbitrary path fragments.
  return `/workspace/ppt-confirmation/${threadId}/${taskId}`;
}

export async function openConfirmationPage(
  threadId: string,
  taskId: string,
): Promise<string> {
  const value = await boundedJSON(
    await fetch(path(threadId, taskId) + "/open", {
      method: "POST",
      body: "{}",
      headers: { "Content-Type": "application/json" },
      redirect: "error",
      signal: AbortSignal.timeout(30000),
    }),
  );
  if (!value || typeof value !== "object")
    throw new Error("invalid_confirmation_open");
  const row = value as Record<string, unknown>;
  const prefix = `/api/threads/${threadId}/live-preview/`;
  if (
    typeof row.url !== "string" ||
    !row.url.startsWith(prefix) ||
    !/^50[5-9][0-9]\/$/.test(row.url.slice(prefix.length)) ||
    row.auto_enabled !== false ||
    row.confirmation_submitted !== false ||
    row.agent_started !== false
  )
    throw new Error("invalid_confirmation_open");
  return row.url;
}
