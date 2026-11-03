import type { Client as LangGraphClient } from "@langchain/langgraph-sdk/client";

export const PPT_MASTER_RUNTIME_ASSISTANT_ID = "ppt-master-runner";
export const PPT_MASTER_RUNTIME_METADATA_KEY = "runtime_profile";

const STORAGE_PREFIX = "deerflow:ppt-master-runtime:";
const rememberedRuntimeThreads = new Set<string>();

function messageText(message: unknown): string {
  if (typeof message !== "object" || message === null) return "";
  const content = Reflect.get(message, "content");
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .flatMap((block) =>
      typeof block === "object" && block !== null
        ? [Reflect.get(block, "text")]
        : [],
    )
    .filter((value): value is string => typeof value === "string")
    .join("\n");
}

function isHumanMessage(message: unknown): boolean {
  if (typeof message !== "object" || message === null) return false;
  const role = Reflect.get(message, "role");
  const type = Reflect.get(message, "type");
  return role === "user" || role === "human" || type === "human";
}

export function isExplicitPptMasterRunInput(input: unknown): boolean {
  if (typeof input !== "object" || input === null) return false;
  const messages = Reflect.get(input, "messages");
  if (!Array.isArray(messages)) return false;
  const latestHuman = [...messages].reverse().find(isHumanMessage);
  return /^\s*\/ppt-master(?:\s|$)/i.test(messageText(latestHuman));
}

export function hasPptMasterRuntimeMetadata(thread: unknown): boolean {
  if (typeof thread !== "object" || thread === null) return false;
  const metadata = Reflect.get(thread, "metadata");
  return (
    typeof metadata === "object" &&
    metadata !== null &&
    Reflect.get(metadata, PPT_MASTER_RUNTIME_METADATA_KEY) ===
      PPT_MASTER_RUNTIME_ASSISTANT_ID
  );
}

function storageKey(threadId: string): string {
  return `${STORAGE_PREFIX}${threadId}`;
}

function rememberThread(threadId: string): void {
  if (typeof window === "undefined") return;
  rememberedRuntimeThreads.add(threadId);
  try {
    window.sessionStorage.setItem(
      storageKey(threadId),
      PPT_MASTER_RUNTIME_ASSISTANT_ID,
    );
  } catch {
    // Keep the current page routed even when enterprise browser policy or an
    // extension denies sessionStorage. Metadata remains the durable source
    // across a later browser session.
  }
}

export function rememberPptMasterRuntimeThread(
  threadId: string,
  input: unknown,
): boolean {
  if (!threadId || !isExplicitPptMasterRunInput(input)) return false;
  rememberThread(threadId);
  return true;
}

function rememberedThread(threadId: string): boolean {
  if (typeof window === "undefined") return false;
  if (rememberedRuntimeThreads.has(threadId)) return true;
  try {
    const remembered =
      window.sessionStorage.getItem(storageKey(threadId)) ===
      PPT_MASTER_RUNTIME_ASSISTANT_ID;
    if (remembered) rememberedRuntimeThreads.add(threadId);
    return remembered;
  } catch {
    return false;
  }
}

/**
 * Carry the isolated-runtime route from the client-generated welcome-page id
 * to the authoritative id returned by the run creation event. LangGraph will
 * normally preserve the submitted id, but the transport contract permits the
 * runtime to return its own id. Losing the marker in that hand-off makes the
 * frontend query the ordinary namespace and produces paired thread/state 404s.
 */
export function inheritPptMasterRuntimeThread(
  sourceThreadId: string | null | undefined,
  targetThreadId: string | null | undefined,
): boolean {
  if (!sourceThreadId || !targetThreadId || !rememberedThread(sourceThreadId)) {
    return false;
  }
  rememberThread(targetThreadId);
  return true;
}

export function getPptMasterRuntimeBaseURL(): string {
  if (typeof window !== "undefined") {
    return `${window.location.origin}/api/ppt-master-runtime`;
  }
  return "http://localhost:2026/api/ppt-master-runtime";
}

function hasPptMasterRuntimeRun(runs: unknown): boolean {
  if (!Array.isArray(runs)) return false;
  return runs.some((run) => {
    if (typeof run !== "object" || run === null) return false;
    if (Reflect.get(run, "assistant_id") === PPT_MASTER_RUNTIME_ASSISTANT_ID) {
      return true;
    }
    const metadata = Reflect.get(run, "metadata");
    return (
      typeof metadata === "object" &&
      metadata !== null &&
      Reflect.get(metadata, PPT_MASTER_RUNTIME_METADATA_KEY) ===
        PPT_MASTER_RUNTIME_ASSISTANT_ID
    );
  });
}

export async function shouldUsePptMasterRuntime(
  client: LangGraphClient,
  threadId: string,
  input?: unknown,
): Promise<boolean> {
  if (rememberPptMasterRuntimeThread(threadId, input)) {
    // The runner's run-creation request persists the runtime profile. Avoid an
    // eager thread PATCH here: a first-submit thread does not exist yet, so the
    // otherwise harmless caught 404 still pollutes the browser console.
    return true;
  }
  if (rememberedThread(threadId)) return true;

  // A submitted input is authoritative for a fresh client-generated thread.
  // Explicit /ppt-master inputs were handled above, and a known runner thread
  // was handled by the remembered marker. Probing runs here would race ahead
  // of the ordinary runtime's first POST /runs/stream, producing a guaranteed
  // 404 for a thread that has not been created yet.
  if (input !== undefined) return false;

  // A full page reload clears the in-memory marker, and enterprise browser
  // policy may also make sessionStorage unavailable. The shared run store is
  // the durable, no-error routing authority: listing a thread with no runs
  // returns []/200, unlike probing the wrong checkpoint namespace with
  // threads.get(), which emits the paired thread/state 404s this adapter is
  // intended to prevent. Older runner threads are recovered from assistant_id
  // even when their thread metadata predates runtime_profile persistence.
  try {
    const runs = await client.runs.list(threadId);
    if (hasPptMasterRuntimeRun(runs)) {
      rememberThread(threadId);
      return true;
    }
  } catch {
    // Preserve the ordinary runtime as the fail-closed owner. Its subsequent
    // request will surface the canonical network/auth error to the caller.
  }
  return false;
}
