import type { Client as LangGraphClient } from "@langchain/langgraph-sdk/client";

export const PPT_MASTER_RUNTIME_ASSISTANT_ID = "ppt-master-runner";
export const PPT_MASTER_RUNTIME_METADATA_KEY = "runtime_profile";

const STORAGE_PREFIX = "deerflow:ppt-master-runtime:";

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
  try {
    window.sessionStorage.setItem(
      storageKey(threadId),
      PPT_MASTER_RUNTIME_ASSISTANT_ID,
    );
  } catch {
    // Metadata remains the durable source when browser storage is unavailable.
  }
}

function rememberedThread(threadId: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return (
      window.sessionStorage.getItem(storageKey(threadId)) ===
      PPT_MASTER_RUNTIME_ASSISTANT_ID
    );
  } catch {
    return false;
  }
}

export function getPptMasterRuntimeBaseURL(): string {
  if (typeof window !== "undefined") {
    return `${window.location.origin}/api/ppt-master-runtime`;
  }
  return "http://localhost:2026/api/ppt-master-runtime";
}

export async function shouldUsePptMasterRuntime(
  client: LangGraphClient,
  threadId: string,
  input?: unknown,
): Promise<boolean> {
  if (isExplicitPptMasterRunInput(input)) {
    rememberThread(threadId);
    try {
      await client.threads.update(threadId, {
        metadata: {
          [PPT_MASTER_RUNTIME_METADATA_KEY]:
            PPT_MASTER_RUNTIME_ASSISTANT_ID,
        },
      });
    } catch {
      // A brand-new thread may be created by the runner moments later. The
      // request metadata and session marker still keep this first run routed.
    }
    return true;
  }
  if (rememberedThread(threadId)) return true;
  try {
    const thread = await client.threads.get(threadId);
    if (hasPptMasterRuntimeMetadata(thread)) {
      rememberThread(threadId);
      return true;
    }
  } catch {
    // Missing/unauthorized threads stay on the ordinary runtime, which owns
    // the canonical error response.
  }
  return false;
}
