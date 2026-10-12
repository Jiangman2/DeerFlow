import { useEffect, useMemo } from "react";

import { fetch } from "../api/fetcher";
import { getBackendBaseURL } from "../config";

import { taskEventToSubtaskUpdate } from "./lifecycle";
import {
  mergePptUsageObservation,
  type PptUsageCarrier,
} from "./ppt-usage-observation";
import type { Subtask } from "./types";

type Update = Partial<Subtask> & PptUsageCarrier & { id: string };
type StoredEvent = { event_type?: unknown; seq?: unknown; content?: unknown };

/** Existing owner-authorized event API only; no session files or model calls. */
export async function fetchPptTaskRecovery(
  threadId: string,
  runId: string,
  signal?: AbortSignal,
): Promise<Update | null> {
  const base = `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/events`;
  let cursor: number | undefined;
  let update: Update | null = null;
  let admitted = false;
  for (let page = 0; page < 32; page++) {
    const params = new URLSearchParams({
      event_types: "subagent.start,subagent.usage,subagent.end",
      task_id: runId,
      limit: "500",
    });
    if (cursor !== undefined) params.set("after_seq", String(cursor));
    const response = await fetch(`${base}?${params}`, { signal });
    if (!response.ok)
      throw new Error(
        `Failed to restore PPT task receipts: ${response.status}`,
      );
    const batch: unknown = await response.json();
    if (!Array.isArray(batch) || batch.length > 500)
      throw new Error("Invalid PPT receipt page");
    for (const raw of batch) {
      const row = raw as StoredEvent;
      if (
        !row ||
        typeof row !== "object" ||
        typeof row.content !== "object" ||
        row.content === null ||
        Array.isArray(row.content)
      )
        continue;
      const content = row.content as Record<string, unknown>;
      if (
        content.task_id !== runId ||
        content.model_name !== "ppt-master-agent-host"
      )
        continue;
      const type =
        row.event_type === "subagent.start"
          ? "task_started"
          : row.event_type === "subagent.usage"
            ? "task_running"
            : row.event_type === "subagent.end"
              ? (
                  {
                    completed: "task_completed",
                    failed: "task_failed",
                    cancelled: "task_cancelled",
                    timed_out: "task_failed",
                  } as Record<string, string>
                )[String(content.status)]
              : undefined;
      if (!type) continue;
      if (type === "task_started") admitted = true;
      const next = taskEventToSubtaskUpdate({ ...content, type });
      if (!next) continue;
      const combined: Update = { ...update, ...next, id: runId };
      update = mergePptUsageObservation(
        update ?? undefined,
        combined,
        next,
      ).next;
    }
    if (batch.length < 500) return admitted ? update : null;
    const last = batch[batch.length - 1] as StoredEvent;
    if (
      typeof last.seq !== "number" ||
      !Number.isSafeInteger(last.seq) ||
      last.seq <= (cursor ?? 0)
    )
      throw new Error("Invalid PPT receipt cursor");
    cursor = last.seq;
  }
  // Do not present a truncated old snapshot as the final state.
  throw new Error("PPT receipt recovery limit reached");
}

/** One bounded backfill on hydration/turn boundary, never browser polling. */
export function usePptTaskRecovery(
  threadId: string | undefined,
  messages: readonly unknown[],
  isLoading: boolean,
  updateSubtask: (task: Update, options?: { eager?: boolean }) => void,
) {
  const key = useMemo(() => {
    const ids = new Set<string>();
    for (const message of messages) {
      if (typeof message !== "object" || message === null) continue;
      const runId = (message as { run_id?: unknown }).run_id;
      if (typeof runId === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(runId))
        ids.add(runId);
    }
    return [...ids].slice(-12).join(",");
  }, [messages]);
  useEffect(() => {
    if (!threadId || !key) return;
    const controller = new AbortController();
    const ids = key.split(",");
    let index = 0;
    const consume = async () => {
      while (index < ids.length && !controller.signal.aborted) {
        const runId = ids[index++];
        if (!runId) continue;
        try {
          const update = await fetchPptTaskRecovery(
            threadId,
            runId,
            controller.signal,
          );
          if (update && !controller.signal.aborted)
            updateSubtask(update, { eager: true });
        } catch {
          // History/SSE remains authoritative if metadata backfill is unavailable.
          // Do not invent zeros or retry indefinitely.
        }
      }
    };
    for (let slot = 0; slot < Math.min(3, ids.length); slot++) void consume();
    return () => controller.abort();
  }, [threadId, key, isLoading, updateSubtask]);
}
