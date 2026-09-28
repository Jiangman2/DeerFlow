/** Version-audited session metadata, explicitly NOT current-run billing usage. */
export type PptSessionCounter = {
  source: "codex_session_token_count";
  cli_version: "0.139.0";
  session_id_hash: string;
  usage: {
    input_tokens: number;
    cached_input_tokens: number;
    output_tokens: number;
    reasoning_output_tokens: number;
    total_tokens: number;
  };
};

export type PptUsageObservation = {
  semantics: "session_cumulative_not_current_turn_or_invoice";
  parent: PptSessionCounter | null;
  workers: PptSessionCounter[];
  worker_count: number | null;
  partial: boolean;
};

export type PptUsageCarrier = {
  sessionUsageObservation?: PptUsageObservation;
};

const SEMANTICS = "session_cumulative_not_current_turn_or_invoice";
const COUNTS = [
  "input_tokens",
  "cached_input_tokens",
  "output_tokens",
  "reasoning_output_tokens",
  "total_tokens",
] as const;

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeCounter(value: unknown): PptSessionCounter | null {
  if (
    !record(value) ||
    value.source !== "codex_session_token_count" ||
    value.cli_version !== "0.139.0" ||
    typeof value.session_id_hash !== "string" ||
    !/^[0-9a-f]{16}$/.test(value.session_id_hash) ||
    !record(value.usage)
  ) {
    return null;
  }
  const counts = value.usage;
  if (
    COUNTS.some(
      (key) =>
        typeof counts[key] !== "number" ||
        !Number.isSafeInteger(counts[key]) ||
        counts[key] < 0,
    )
  ) {
    return null;
  }
  const usage = Object.fromEntries(
    COUNTS.map((key) => [key, counts[key]]),
  ) as PptSessionCounter["usage"];
  if (
    usage.total_tokens !== usage.input_tokens + usage.output_tokens ||
    usage.cached_input_tokens > usage.input_tokens ||
    usage.reasoning_output_tokens > usage.output_tokens
  ) {
    return null;
  }
  return {
    source: "codex_session_token_count",
    cli_version: "0.139.0",
    session_id_hash: value.session_id_hash,
    usage,
  };
}

export function normalizePptUsageObservation(
  value: unknown,
): PptUsageObservation | undefined {
  if (!record(value) || value.semantics !== SEMANTICS) {
    return undefined;
  }
  const parent = normalizeCounter(value.parent);
  const seen = new Set(parent ? [parent.session_id_hash] : []);
  const workers: PptSessionCounter[] = [];
  if (Array.isArray(value.workers)) {
    for (const raw of value.workers.slice(0, 7)) {
      const worker = normalizeCounter(raw);
      if (worker && !seen.has(worker.session_id_hash)) {
        workers.push(worker);
        seen.add(worker.session_id_hash);
      }
    }
  }
  workers.sort((a, b) => a.session_id_hash.localeCompare(b.session_id_hash));
  const count =
    typeof value.worker_count === "number" &&
    Number.isSafeInteger(value.worker_count) &&
    value.worker_count >= 0 &&
    value.worker_count <= 64
      ? value.worker_count
      : null;
  return {
    semantics: SEMANTICS,
    parent,
    workers,
    worker_count: count,
    partial:
      value.partial === true ||
      parent === null ||
      count === null ||
      workers.length !== count,
  };
}

/** Merge separately, so no observation can alter `Subtask.usage`. */
export function mergePptUsageObservation<T extends object>(
  previous: (T & PptUsageCarrier) | undefined,
  next: T,
  update: PptUsageCarrier,
): { next: T & PptUsageCarrier; changed: boolean } {
  const incoming = normalizePptUsageObservation(update.sessionUsageObservation);
  if (!incoming) {
    return {
      next: {
        ...next,
        ...(previous?.sessionUsageObservation
          ? { sessionUsageObservation: previous.sessionUsageObservation }
          : {}),
      },
      changed: false,
    };
  }
  const old = previous?.sessionUsageObservation;
  const oldCounters = new Map(
    [old?.parent, ...(old?.workers ?? [])]
      .filter((counter): counter is PptSessionCounter => Boolean(counter))
      .map((counter) => [counter.session_id_hash, counter]),
  );
  const retainNewer = (counter: PptSessionCounter) => {
    const prior = oldCounters.get(counter.session_id_hash);
    return prior && prior.usage.total_tokens > counter.usage.total_tokens
      ? prior
      : counter;
  };
  const merged: PptUsageObservation = {
    ...incoming,
    parent: incoming.parent ? retainNewer(incoming.parent) : null,
    workers: incoming.workers.map(retainNewer),
  };
  const changed = JSON.stringify(old) !== JSON.stringify(merged);
  return {
    next: { ...next, sessionUsageObservation: changed ? merged : old },
    changed,
  };
}
