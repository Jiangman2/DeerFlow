/**
 * Return the owning DeerFlow thread id carried by a streamed custom event.
 *
 * New Gateway builds attach this field to task lifecycle events. The metadata
 * fallback keeps the client compatible with transports that wrap the event
 * envelope before it reaches the LangGraph SDK.
 */
export function customEventThreadId(event: unknown): string | undefined {
  if (!isRecord(event)) {
    return undefined;
  }

  const direct = firstNonEmptyString(event.thread_id, event.threadId);
  if (direct) {
    return direct;
  }

  if (isRecord(event.metadata)) {
    return firstNonEmptyString(
      event.metadata.thread_id,
      event.metadata.threadId,
    );
  }

  return undefined;
}

/**
 * Accept an event only when it belongs to the active chat view.
 *
 * Older Gateway streams did not carry thread_id. They remain accepted for an
 * existing thread for backwards compatibility, but unscoped events are
 * rejected on the unsaved/new-chat view so a late event from the previous
 * conversation cannot create a task card there.
 */
export function isCustomEventForThread(
  event: unknown,
  activeThreadIds: ReadonlySet<string>,
  activeViewThreadId: string | null,
  isNewThread = false,
): boolean {
  const eventThreadId = customEventThreadId(event);
  if (eventThreadId) {
    return activeThreadIds.has(eventThreadId);
  }
  // A new chat has a client-generated UUID before the backend thread exists.
  // It must not be treated as an existing-chat scope for legacy unscoped
  // events, otherwise a running PPT task from the previous chat leaks into
  // the new page. Once this new run is created, isNewThread becomes false.
  return !isNewThread && activeViewThreadId !== null;
}

function firstNonEmptyString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
