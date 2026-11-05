/**
 * Subtask step model shared by the live (SSE) and reload (fetched) paths.
 *
 * Issue #3779: the subtask card used to keep only the latest subagent message,
 * so earlier steps flashed by and nothing survived a reload. A `SubtaskStep` is
 * the normalized, renderable unit of subagent progress — one assistant turn
 * (`kind: "ai"`, carrying its tool-call requests) or one tool result
 * (`kind: "tool"`, carrying the tool's output). The backend persists the same
 * shape as `subagent.step` run-event content; `messageToStep` mirrors that
 * shaping for the live `task_running` event, which still carries the raw message.
 */

export interface SubtaskStepToolCall {
  name?: string;
  args?: unknown;
}

export interface SubtaskStep {
  message_index: number;
  kind: "ai" | "tool";
  text: string;
  truncated?: boolean;
  tool_calls?: SubtaskStepToolCall[];
  tool_name?: string;
}

type RawMessage = {
  type?: string;
  content?: unknown;
  name?: string;
  tool_calls?: { name?: string; args?: unknown; [key: string]: unknown }[];
  [key: string]: unknown;
};

function contentToText(content: unknown): string {
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") {
          return block;
        }
        if (block && typeof block === "object" && "text" in block) {
          const text = (block as { text?: unknown }).text;
          return typeof text === "string" ? text : "";
        }
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

/** Normalize a raw subagent message (live `task_running` payload) into a step. */
export function messageToStep(
  message: RawMessage,
  messageIndex: number,
): SubtaskStep {
  const kind = message.type === "tool" ? "tool" : "ai";
  const step: SubtaskStep = {
    message_index: messageIndex,
    kind,
    text: contentToText(message.content),
  };

  if (kind === "tool") {
    step.tool_name = message.name;
  } else {
    step.tool_calls = (message.tool_calls ?? []).map((call) => ({
      name: call.name,
      args: call.args,
    }));
  }

  return step;
}

/**
 * Steps to render in the subtask card timeline (#3779). Interleaves the
 * subagent's assistant turns and tool steps, ordered by `message_index`:
 *
 * - tool steps are always kept (one "the subagent ran <tool>" row each);
 * - AI steps are kept when they carry visible text or tool-call inputs.  The
 *   latter are required for an auditable timeline: a following tool result has
 *   the output and tool name, but not the command/query/path that produced it;
 * - when the task is `completed`, a trailing AI step with no tool_calls is the
 *   subagent's final answer, which the card already renders as `task.result`,
 *   so it is dropped here to avoid showing the answer twice.
 */
export function stepsForDisplay(
  steps: SubtaskStep[] | undefined,
  status: "in_progress" | "completed" | "failed" | "incomplete",
): SubtaskStep[] {
  const visible = (steps ?? [])
    .filter(
      (step) =>
        step.kind === "tool" ||
        step.text.trim() !== "" ||
        Boolean(step.tool_calls?.length),
    )
    .sort((a, b) => a.message_index - b.message_index);

  if (status === "completed") {
    const last = visible[visible.length - 1];
    if (last?.kind === "ai" && !last?.tool_calls?.length) {
      return visible.slice(0, -1);
    }
  }
  return visible;
}

type RunEvent = {
  event_type?: string;
  content?: unknown;
  metadata?: { task_id?: string } & Record<string, unknown>;
};

/**
 * Map persisted run events (from `GET /{rid}/events`) into the subtask's steps,
 * keeping only `subagent.step` events for `taskId` and ordering by message_index.
 * The persisted `content` already matches the step shape (it is what the backend
 * `build_subagent_step` produced), so this filters, projects, and sorts (#3779).
 */
export function eventsToSteps(
  events: RunEvent[],
  taskId: string,
): SubtaskStep[] {
  const steps: SubtaskStep[] = [];
  for (const event of events) {
    if (event.event_type !== "subagent.step") {
      continue;
    }
    const content = event.content as
      | (SubtaskStep & { task_id?: string })
      | undefined;
    const eventTaskId = content?.task_id ?? event.metadata?.task_id;
    if (!content || eventTaskId !== taskId) {
      continue;
    }
    steps.push({
      message_index: content.message_index,
      kind: content.kind,
      text: content.text ?? "",
      truncated: content.truncated,
      tool_calls: content.tool_calls,
      tool_name: content.tool_name,
    });
  }
  return steps.sort((a, b) => a.message_index - b.message_index);
}

/**
 * Merge `incoming` steps into `existing`, deduping by `message_index` (incoming
 * wins) and keeping the result ordered. Used to reconcile live SSE steps with
 * steps fetched on expand without double-rendering shared indices.
 */
export function mergeSteps(
  existing: SubtaskStep[],
  incoming: SubtaskStep[],
): SubtaskStep[] {
  const byIndex = new Map<number, SubtaskStep>();
  for (const step of existing) {
    byIndex.set(step.message_index, step);
  }
  for (const step of incoming) {
    byIndex.set(step.message_index, step);
  }
  const merged = [...byIndex.values()].sort(
    (a, b) => a.message_index - b.message_index,
  );
  return stepListsEqual(existing, merged) ? existing : merged;
}

function stepListsEqual(a: SubtaskStep[], b: SubtaskStep[]): boolean {
  if (a === b) {
    return true;
  }
  if (a.length !== b.length) {
    return false;
  }
  return a.every((step, index) => stepEquals(step, b[index]));
}

function stepEquals(a: SubtaskStep, b: SubtaskStep | undefined): boolean {
  if (!b) {
    return false;
  }
  if (
    a.message_index !== b.message_index ||
    a.kind !== b.kind ||
    a.text !== b.text ||
    a.truncated !== b.truncated ||
    a.tool_name !== b.tool_name
  ) {
    return false;
  }

  const aCalls = a.tool_calls ?? [];
  const bCalls = b.tool_calls ?? [];
  if (aCalls.length !== bCalls.length) {
    return false;
  }
  return aCalls.every((call, index) => {
    const other = bCalls[index];
    return (
      call.name === other?.name &&
      (call.args === other?.args ||
        JSON.stringify(call.args) === JSON.stringify(other?.args))
    );
  });
}
