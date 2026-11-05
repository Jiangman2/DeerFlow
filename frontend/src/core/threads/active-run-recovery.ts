import type { Run } from "@langchain/langgraph-sdk";

const ACTIVE_RUN_STATUSES = new Set<Run["status"]>(["pending", "running"]);

function runTimestamp(run: Run): number {
  const value = Date.parse(run.updated_at ?? run.created_at);
  return Number.isFinite(value) ? value : 0;
}

/** Return the newest run that can still emit stream events. */
export function findLatestActiveRun(runs: readonly Run[]): Run | undefined {
  let latest: Run | undefined;
  for (const run of runs) {
    if (!ACTIVE_RUN_STATUSES.has(run.status)) continue;
    if (!latest || runTimestamp(run) > runTimestamp(latest)) {
      latest = run;
    }
  }
  return latest;
}

export function readReconnectRun(threadId: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(`lg:stream:${threadId}`);
  } catch {
    return null;
  }
}

export function rememberReconnectRun(threadId: string, runId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(`lg:stream:${threadId}`, runId);
  } catch {
    // A blocked storage API must not prevent an authoritative backend rejoin.
  }
}
