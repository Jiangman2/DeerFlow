import { normalizeTokenUsage } from "../messages/usage";

import type { Subtask } from "./types";

type TaskStartedEvent = {
  type: "task_started";
  task_id: string;
  description?: unknown;
  model_name?: unknown;
};

type TaskRunningEvent = {
  type: "task_running";
  task_id: string;
  model_name?: unknown;
  usage?: unknown;
};

type TaskTerminalEvent = {
  type: "task_completed" | "task_failed" | "task_cancelled";
  task_id: string;
  result?: unknown;
  error?: unknown;
  model_name?: unknown;
};

/** Convert an additive task lifecycle event into a task-state update. */
export function taskEventToSubtaskUpdate(
  event: unknown,
): (Partial<Subtask> & { id: string }) | null {
  if (!isRecord(event)) {
    return null;
  }

  const taskId = event.task_id;
  if (typeof taskId !== "string" || !taskId.trim()) {
    return null;
  }

  if (event.type === "task_started") {
    const started = event as TaskStartedEvent;
    const modelName = normalizeText(started.model_name);
    const description =
      normalizeText(started.description) ?? "ppt-master 官方工作流";
    return {
      id: taskId,
      status: "in_progress",
      subagent_type: "ppt-master",
      description,
      prompt: "",
      ...(modelName ? { modelName } : {}),
    };
  }

  if (event.type === "task_running") {
    const running = event as TaskRunningEvent;
    const usage = normalizeTokenUsage(running.usage);
    const modelName = normalizeModelName(running.model_name);
    return usage || modelName
      ? {
          id: taskId,
          ...(modelName ? { modelName } : {}),
          ...(usage ? { usage } : {}),
        }
      : null;
  }

  if (
    event.type === "task_completed" ||
    event.type === "task_failed" ||
    event.type === "task_cancelled"
  ) {
    const terminal = event as TaskTerminalEvent;
    const modelName = normalizeModelName(terminal.model_name);
    const result = normalizeText(terminal.result);
    const error = normalizeText(terminal.error);
    return {
      id: taskId,
      status:
        terminal.type === "task_completed"
          ? "completed"
          : terminal.type === "task_cancelled"
            ? "incomplete"
            : "failed",
      ...(modelName ? { modelName } : {}),
      ...(result ? { result } : {}),
      ...(error ? { error } : {}),
    };
  }

  return null;
}

/** Restore the external ppt-master task card from durable AI-message metadata. */
export function pptMasterTaskMessageToSubtaskUpdate(
  message: unknown,
): (Partial<Subtask> & { id: string }) | null {
  if (!isRecord(message) || !isRecord(message.additional_kwargs)) {
    return null;
  }
  const pointer = message.additional_kwargs.ppt_master_task;
  if (!isRecord(pointer)) {
    return null;
  }
  const taskId = normalizeText(pointer.task_id);
  const status = pointer.status;
  if (
    !taskId ||
    (status !== "completed" && status !== "failed" && status !== "incomplete")
  ) {
    return null;
  }
  const result = normalizeText(pointer.result);
  const error = normalizeText(pointer.error);
  const steps = normalizeSteps(pointer.steps);
  return {
    id: taskId,
    status,
    subagent_type: "ppt-master",
    description: normalizeText(pointer.description) ?? "ppt-master 官方工作流",
    prompt: "",
    modelName: normalizeText(pointer.model_name) ?? "ppt-master-agent-host",
    ...(result ? { result } : {}),
    ...(error ? { error } : {}),
    ...(steps ? { steps } : {}),
  };
}

function normalizeSteps(value: unknown): NonNullable<Subtask["steps"]> | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }

  const steps: NonNullable<Subtask["steps"]> = [];
  for (const rawStep of value) {
    if (!isRecord(rawStep)) {
      continue;
    }
    const messageIndex = rawStep.message_index;
    const kind = rawStep.kind;
    if (
      typeof messageIndex !== "number" ||
      !Number.isFinite(messageIndex) ||
      (kind !== "ai" && kind !== "tool")
    ) {
      continue;
    }

    const toolCalls = Array.isArray(rawStep.tool_calls)
      ? rawStep.tool_calls
          .filter(isRecord)
          .map((call) => ({
            ...(typeof call.name === "string" ? { name: call.name } : {}),
            ...(Object.hasOwn(call, "args") ? { args: call.args } : {}),
          }))
      : undefined;
    steps.push({
      message_index: messageIndex,
      kind,
      text: typeof rawStep.text === "string" ? rawStep.text : "",
      ...(typeof rawStep.truncated === "boolean"
        ? { truncated: rawStep.truncated }
        : {}),
      ...(toolCalls ? { tool_calls: toolCalls } : {}),
      ...(typeof rawStep.tool_name === "string"
        ? { tool_name: rawStep.tool_name }
        : {}),
    });
  }

  return steps.length > 0
    ? steps.sort((a, b) => a.message_index - b.message_index)
    : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function normalizeModelName(value: unknown): string | undefined {
  return normalizeText(value);
}

function normalizeText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
