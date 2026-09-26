import type { AgentSource } from "./types";

/**
 * Next.js exposes dynamic path segments in their URL-encoded form in some
 * browser/navigation paths. Decode exactly once before using an agent name as
 * display text, an API argument, or LangGraph run context.
 */
export function decodeAgentNameRouteParam(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    // Preserve malformed or literal-percent legacy names instead of making
    // the custom-agent page fail during render.
    return value;
  }
}

export function parseAgentSource(value: unknown): AgentSource | undefined {
  if (value === undefined || value === null) return undefined;
  if (value === "personal" || value === "team" || value === "legacy")
    return value;
  throw new Error("Invalid digital employee source");
}

export function agentChatPath(
  name: string,
  threadId: string,
  source?: AgentSource | null,
): string {
  const parsed = parseAgentSource(source);
  const suffix = parsed ? `?source=${parsed}` : "";
  return `/workspace/agents/${encodeURIComponent(name)}/chats/${threadId}${suffix}`;
}
