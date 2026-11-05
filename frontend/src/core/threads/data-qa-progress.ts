export const DATA_QA_STAGES = [
  "waiting",
  "processing",
  "answering",
  "completed",
  "empty",
  "clarification",
  "failed",
  "timeout",
  "cancelled",
  "stopped",
] as const;
export type DataQAStage = (typeof DATA_QA_STAGES)[number];
export type DataQAProgress = {
  threadId: string;
  runId: string;
  callId: string;
  sequence: number;
  stages: DataQAStage[];
};
export type DataQAProgressMap = Record<string, DataQAProgress>;
export type DataQAProgressCarrier = { dataQAProgress?: DataQAProgressMap };

export function isDataQARunning(stage: DataQAStage) {
  return stage === "waiting" || stage === "processing" || stage === "answering";
}

export function mergeDataQAProgress(
  previous: DataQAProgressMap,
  event: unknown,
  activeRunId: string | null,
): DataQAProgressMap {
  if (!event || typeof event !== "object") return previous;
  const e = event as Record<string, unknown>;
  if (
    e.type !== "data_qa_progress" ||
    e.tool !== "datalake_data_qa" ||
    !activeRunId ||
    e.run_id !== activeRunId ||
    typeof e.thread_id !== "string" ||
    !e.thread_id ||
    e.thread_id.length > 256 ||
    typeof e.tool_call_id !== "string" ||
    !e.tool_call_id ||
    e.tool_call_id.length > 256 ||
    typeof e.sequence !== "number" ||
    !Number.isSafeInteger(e.sequence) ||
    e.sequence < 1 ||
    e.sequence > 16 ||
    !DATA_QA_STAGES.includes(e.stage as DataQAStage) ||
    e.stage === "stopped"
  )
    return previous;
  const stage = e.stage as DataQAStage;
  const key = JSON.stringify([e.thread_id, e.tool_call_id]);
  const old = previous[key];
  if (
    old?.runId === activeRunId &&
    (old.sequence >= e.sequence || !isDataQARunning(old.stages.at(-1)!))
  )
    return previous;
  const stages = old?.runId === activeRunId ? old.stages : [];
  const bounded = Object.fromEntries(Object.entries(previous).slice(-99));
  return {
    ...bounded,
    [key]: {
      threadId: e.thread_id,
      runId: activeRunId,
      callId: e.tool_call_id,
      sequence: e.sequence,
      stages: stages.includes(stage) ? stages : [...stages, stage].slice(-8),
    },
  };
}

export function settleDataQAProgress(
  previous: DataQAProgressMap,
  stage: "cancelled" | "failed" | "stopped",
  runId: string | null,
) {
  return Object.fromEntries(
    Object.entries(previous).map(([key, value]) => [
      key,
      value.runId === runId &&
      (isDataQARunning(value.stages.at(-1)!) ||
        (stage === "cancelled" && value.stages.at(-1) === "stopped"))
        ? { ...value, stages: [...value.stages, stage].slice(-8) }
        : value,
    ]),
  );
}

export function dataQAResultStage(result: unknown): DataQAStage | undefined {
  let value = result;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return "failed";
    }
  }
  if (!value || typeof value !== "object") return undefined;
  const r = value as Record<string, unknown>;
  if (r.error || r.status === "failed")
    return r.code === "ReadTimeout" || r.code === "ConnectTimeout"
      ? "timeout"
      : "failed";
  if (r.status === "interrupted") return "clarification";
  if (r.status === "empty") return "empty";
  return r.status === "done" ? "completed" : undefined;
}
