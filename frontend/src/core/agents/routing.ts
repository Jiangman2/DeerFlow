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
