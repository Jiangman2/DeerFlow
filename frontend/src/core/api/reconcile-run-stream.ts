/** Close a stranded transport only after the exact run and its final state are read. */
export const RUN_RECONCILE_INTERVAL_MS = 15_000;
const READ_TIMEOUT_MS = 8_000;
const TERMINAL_STATUSES = new Set([
  "success",
  "error",
  "timeout",
  "interrupted",
]);

type Identity = { thread_id: string; run_id: string };
type Event = { event: string; data: unknown };

export async function* reconcileRunStream<T extends Event>(options: {
  start: (signal: AbortSignal) => AsyncIterable<T>;
  identity: () => Identity | undefined;
  getRun: (
    identity: Identity,
    signal: AbortSignal,
  ) => Promise<Identity & { status: string }>;
  getValues: (identity: Identity, signal: AbortSignal) => Promise<unknown>;
  signal?: AbortSignal;
}): AsyncGenerator<T | { event: "values"; data: unknown }> {
  const transport = new AbortController();
  let disposed = false;
  let checking = false;
  let read: AbortController | undefined;
  let readTimer: ReturnType<typeof setTimeout> | undefined;
  type Control = { kind: "terminal"; values: unknown } | { kind: "aborted" };
  let control: Control | undefined;
  let wake: ((result: Control) => void) | undefined;
  const abort = () => {
    transport.abort();
    read?.abort();
    control = { kind: "aborted" };
    wake?.(control);
  };
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) abort();

  const check = async () => {
    const identity = options.identity();
    if (disposed || checking || transport.signal.aborted || !identity) return;
    checking = true;
    read = new AbortController();
    readTimer = setTimeout(() => read?.abort(), READ_TIMEOUT_MS);
    try {
      const run = await options.getRun(identity, read.signal);
      if (
        run.thread_id !== identity.thread_id ||
        run.run_id !== identity.run_id ||
        !TERMINAL_STATUSES.has(run.status) ||
        read.signal.aborted ||
        disposed
      )
        return;
      // Never drop the last buffered answer/artifact when closing a half-open SSE.
      const values = await options.getValues(identity, read.signal);
      if (
        values == null ||
        disposed ||
        read.signal.aborted ||
        transport.signal.aborted
      )
        return;
      const current = options.identity();
      if (
        current?.thread_id !== identity.thread_id ||
        current.run_id !== identity.run_id
      )
        return;
      control = { kind: "terminal", values };
      wake?.(control);
    } catch {
      // A timeout, 404, or auth/network error is not evidence that a run ended.
    } finally {
      clearTimeout(readTimer);
      read = undefined;
      checking = false;
    }
  };
  const pageActive = () => {
    if (
      typeof document !== "undefined" &&
      document.visibilityState === "visible"
    )
      void check();
  };
  const timer = setInterval(() => void check(), RUN_RECONCILE_INTERVAL_MS);
  if (typeof window !== "undefined")
    window.addEventListener?.("focus", pageActive);
  if (typeof document !== "undefined")
    document.addEventListener?.("visibilitychange", pageActive);
  const iterator = options.start(transport.signal)[Symbol.asyncIterator]();
  try {
    if (options.signal?.aborted)
      throw new DOMException("Aborted", "AbortError");
    while (!transport.signal.aborted) {
      // Use a fresh waiter for each chunk: repeatedly racing one unresolved
      // completion promise would retain a handler per token for long runs.
      const notification = new Promise<Control>((resolve) => {
        wake = resolve;
      });
      // Keep exactly one outstanding stream read. The polling task never reads SSE.
      const next = iterator
        .next()
        .then((value) => ({ kind: "event" as const, value }));
      const result = await Promise.race([next, control ?? notification]);
      wake = undefined;
      if (result.kind === "aborted")
        throw new DOMException("Aborted", "AbortError");
      if (result.kind === "terminal") {
        // Abort only this HTTP transport, never client.runs.cancel / SDK stop.
        // Return normally so useStream hydrates history, calls onFinish and
        // releases isLoading through its existing success/finally path.
        transport.abort();
        yield { event: "values", data: result.values };
        return;
      }
      if (result.value.done) return;
      yield result.value.value;
    }
    if (options.signal?.aborted)
      throw new DOMException("Aborted", "AbortError");
  } finally {
    disposed = true;
    clearInterval(timer);
    clearTimeout(readTimer);
    read?.abort();
    transport.abort();
    options.signal?.removeEventListener("abort", abort);
    if (typeof window !== "undefined")
      window.removeEventListener?.("focus", pageActive);
    if (typeof document !== "undefined")
      document.removeEventListener?.("visibilitychange", pageActive);
    // A broken reader must not hold UI completion hostage. Its pending next()
    // already has a rejection handler through Promise.race.
    void iterator.return?.().catch(() => undefined);
  }
}
