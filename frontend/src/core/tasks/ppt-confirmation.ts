/** Read-only UI projection of the official owner's public health/recommendations.
 * This adapter does not decide stages, write receipts or submit confirmations.
 */
export type PublicStep = {
  message_index: number;
  kind: string;
  text: string;
  tool_calls?: { args?: unknown }[];
};
type ObjectValue = Record<string, unknown>;
export type ExecutionNotice =
  | "stop_unconfirmed"
  | "model_deadline"
  | "tool_arguments"
  | "failed"
  | "incomplete"
  | "turn_ended";

/** Presentation only: never infer task termination from model progress prose. */
export function executionNotice(task: {
  status: string;
  error?: string;
}): ExecutionNotice | null {
  if (task.status === "in_progress") return null;
  const error =
    typeof task.error === "string" && task.error.length <= 8192
      ? task.error
      : "";
  if (
    error.includes("无法核实外部 Agent 已退出") ||
    error.includes("PPT terminal process cleanup could not be confirmed")
  )
    return "stop_unconfirmed";
  if (task.status === "failed") {
    if (error.includes("Enterprise DeepSeek exceeded the total turn deadline."))
      return "model_deadline";
    if (error.includes("Enterprise DeepSeek tool arguments failed validation"))
      return "tool_arguments";
    return "failed";
  }
  return task.status === "completed" ? "turn_ended" : "incomplete";
}

export type ConfirmationView = {
  state: "needs_confirmation" | "preparing" | "confirmed" | "owner_error";
  stage: "stage1" | "stage2" | "final";
  confirmedStage: "stage1" | "final" | null;
  version: string | null;
  summary: { key: string; value: string }[];
};

function object(value: unknown): ObjectValue {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as ObjectValue)
    : {};
}

function text(value: unknown): string {
  const unwrapped = object(value).value ?? value;
  return typeof unwrapped === "string"
    ? unwrapped
        .replace(/[\u0000-\u0008\u000b-\u001f]/g, "")
        .slice(0, 1200)
        .trim()
    : "";
}

export function confirmationOwner(
  steps: readonly PublicStep[],
): { port: string; project: string } | null {
  let project: string | null = null;
  let port: string | null = null;
  // Sort a copy: live/backfill arrival order is not durable event order.
  for (const step of [...steps].sort(
    (a, b) => a.message_index - b.message_index,
  )) {
    for (const call of step.tool_calls ?? []) {
      const command = object(call.args).command;
      if (typeof command !== "string" || command.length > 12000) continue;
      const match =
        /(?:^|[\s/])confirm_ui\/server\.py\s+(?:"(\/mnt\/user-data\/(?:projects|workspace)\/[^"\r\n]+)"|'(\/mnt\/user-data\/(?:projects|workspace)\/[^'\r\n]+)'|(\/mnt\/user-data\/(?:projects|workspace)\/[^\s'";|&]+))/.exec(
          command,
        );
      const candidate = match?.[1] ?? match?.[2] ?? match?.[3];
      if (
        candidate &&
        !candidate
          .split("/")
          .some((part) => part === ".." || part.startsWith(".")) &&
        !/[$`]/.test(candidate)
      ) {
        if (project !== candidate) port = null;
        project = candidate;
      }
    }
    if (!project) continue;
    // No arbitrary target from model text. Only fixed loopback port discovery;
    // the server's project and service identity are independently checked below.
    for (const match of step.text.matchAll(
      /https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):(50[5-9]\d)(?=$|[/?#\s*`)。，])/g,
    )) {
      port = match[1] ?? null;
    }
  }
  return project && port ? { project, port } : null;
}

export function previewPorts(steps: readonly PublicStep[]): string[] {
  const result = new Set<string>();
  for (const step of [...steps].sort(
    (a, b) => a.message_index - b.message_index,
  )) {
    for (const match of step.text.matchAll(
      /https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):(50[5-9]\d|60[6-9]\d|610\d)(?=$|[/?#\s*`)。，])/g,
    )) {
      const port = match[1];
      if (port) {
        result.delete(port);
        result.add(port);
      }
    }
  }
  return [...result].slice(-4);
}

export function officialConfirmation(
  health: unknown,
  project: string,
  recommendations?: unknown,
): ConfirmationView {
  const body = object(health);
  if (
    body.service !== "confirm_ui" ||
    body.status !== "ok" ||
    body.project !== project
  )
    throw new Error("owner_identity");
  const session = object(body.session);
  const stage = session.current_stage;
  if (stage !== "stage1" && stage !== "stage2" && stage !== "final")
    throw new Error("owner_stage");
  const confirmedStage =
    session.result_stage === "stage1" || session.result_stage === "final"
      ? session.result_stage
      : null;
  const version =
    typeof session.recommendation_version === "string" &&
    /^[a-f0-9]{64}$/.test(session.recommendation_version)
      ? session.recommendation_version
      : null;
  const view: ConfirmationView = {
    state: "owner_error",
    stage,
    confirmedStage,
    version,
    summary: [],
  };
  if (session.status === "done" && confirmedStage === "final")
    return { ...view, state: "confirmed" };
  if (session.status === "waiting_agent")
    return { ...view, state: "preparing" };
  if (session.status === "error") return view;
  if (session.status !== "ready_user" || stage === "final" || !version)
    throw new Error("owner_state");
  if (recommendations === undefined)
    return { ...view, state: "needs_confirmation" };
  const data = object(recommendations);
  if (data.stage !== stage) throw new Error("recommendation_stage");
  const keys =
    stage === "stage1"
      ? [
          "audience",
          "communication_intent",
          "audience_outcome",
          "core_message",
          "delivery_context",
          "artifact_afterlife",
          "content_divergence",
        ]
      : [
          "page_count",
          "image_notes",
          "template_application",
          "design_spec_depth",
        ];
  const summary = keys.map((key) => ({ key, value: text(data[key]) }));
  if (stage === "stage1") {
    summary.push({ key: "canvas", value: text(object(data.recommend).canvas) });
    const options = object(data.template_options);
    summary.push({
      key: "template_mode",
      value:
        options.default_mode === "free_design"
          ? "free_design"
          : options.default_mode === "templates"
            ? "templates"
            : "",
    });
    if (Array.isArray(options.preselected_keys))
      summary.push({
        key: "preselected_count",
        value: String(options.preselected_keys.length),
      });
  } else {
    const directions = object(data.design_directions);
    if (Array.isArray(directions.candidates)) {
      for (const [index, entry] of directions.candidates
        .slice(0, 3)
        .entries()) {
        const candidate = object(entry);
        summary.push({
          key: `direction_${index + 1}`,
          value: [text(candidate.name), text(candidate.note)]
            .filter(Boolean)
            .join("："),
        });
      }
    }
  }
  return { ...view, state: "needs_confirmation", summary };
}

export function sameOwnerVersion(
  left: ConfirmationView,
  right: ConfirmationView,
): boolean {
  return (
    left.stage === right.stage &&
    left.state === right.state &&
    left.version === right.version &&
    left.confirmedStage === right.confirmedStage
  );
}

/** Prefer explicit run order; do not infer recency from random UUIDs or map order. */
export function currentPptTaskId(
  tasks: Record<string, { id: string; modelName?: string; status: string }>,
  messages: readonly unknown[],
): string | null {
  const order: string[] = [];
  for (const raw of messages) {
    const message = object(raw);
    const pointer = object(
      object(message.additional_kwargs).ppt_master_task,
    ).task_id;
    const id = typeof pointer === "string" ? pointer : message.run_id;
    if (typeof id === "string" && order[order.length - 1] !== id)
      order.push(id);
  }
  const latest = order[order.length - 1];
  const candidates = Object.values(tasks).filter(
    (task) => task.modelName === "ppt-master-agent-host",
  );
  const active = candidates.filter((task) => task.status === "in_progress");
  // SSE task_started can precede the first saved message in a new run. A
  // single active PPT receipt outranks the known terminal historical PPT run;
  // never pick by random UUID/order and never cross an unrelated latest run.
  if (latest) {
    const known = tasks[latest];
    if (known?.modelName !== "ppt-master-agent-host") return null;
    if (known.status !== "in_progress" && active.length === 1)
      return active[0]?.id ?? null;
    return latest;
  }
  if (order.length === 0 && active.length === 1) return active[0]?.id ?? null;
  return order.length === 0 && candidates.length === 1
    ? (candidates[0]?.id ?? null)
    : null;
}
