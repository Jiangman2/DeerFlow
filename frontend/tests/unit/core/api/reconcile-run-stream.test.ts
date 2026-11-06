import { afterEach, expect, rs, test } from "@rstest/core";

import {
  reconcileRunStream,
  RUN_RECONCILE_INTERVAL_MS,
} from "@/core/api/reconcile-run-stream";

afterEach(() => {
  rs.useRealTimers();
  rs.unstubAllGlobals();
});

const id = { thread_id: "thread-a", run_id: "run-a" };
const finalValues = {
  messages: [{ id: "final", type: "ai", content: "Done" }],
  artifacts: ["/outputs/report.pdf"],
};

function setup(status = "running") {
  rs.useFakeTimers();
  const controller = new AbortController();
  let transport: AbortSignal | undefined;
  const next = rs.fn(
    () =>
      new Promise<IteratorResult<{ event: string; data: unknown }>>(() => {
        /* Deliberately stranded stream read. */
      }),
  );
  const close = rs.fn(async () => ({ done: true as const, value: undefined }));
  const identity = rs.fn(() => id as typeof id | undefined);
  const getRun = rs.fn(async (_id: typeof id, _signal: AbortSignal) => ({
    ...id,
    status,
  }));
  const getValues = rs.fn(async () => finalValues);
  const stream = reconcileRunStream({
    start(signal) {
      transport = signal;
      return { [Symbol.asyncIterator]: () => ({ next, return: close }) };
    },
    identity,
    getRun,
    getValues,
    signal: controller.signal,
  });
  return {
    stream,
    controller,
    getRun,
    getValues,
    identity,
    next,
    close,
    transport: () => transport,
  };
}

for (const status of ["success", "error", "timeout", "interrupted"]) {
  test(`half-open ${status} stream hydrates final values and ends normally`, async () => {
    const s = setup(status);
    const pending = s.stream.next();
    await rs.advanceTimersByTimeAsync(RUN_RECONCILE_INTERVAL_MS);
    expect(await pending).toEqual({
      done: false,
      value: { event: "values", data: finalValues },
    });
    expect(s.getRun).toHaveBeenCalledWith(id, expect.any(AbortSignal));
    expect(s.transport()?.aborted).toBe(true);
    expect((await s.stream.next()).done).toBe(true);
    expect(s.next).toHaveBeenCalledTimes(1);
    expect(s.close).toHaveBeenCalledTimes(1);
    await rs.advanceTimersByTimeAsync(60_000);
    expect(s.getRun).toHaveBeenCalledTimes(1);
  });
}

for (const status of ["running", "pending", "unknown"]) {
  test(`does not finish a ${status} run because time elapsed`, async () => {
    const s = setup(status);
    const pending = s.stream.next();
    await rs.advanceTimersByTimeAsync(60_000);
    expect(s.transport()?.aborted).toBe(false);
    expect(s.getValues).not.toHaveBeenCalled();
    expect(s.next).toHaveBeenCalledTimes(1);
    s.controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
}

test("failed status and final-state reads remain retryable", async () => {
  const s = setup("success");
  s.getRun.mockRejectedValueOnce(new Error("403"));
  s.getValues.mockRejectedValueOnce(new Error("offline"));
  const pending = s.stream.next();
  await rs.advanceTimersByTimeAsync(30_000);
  expect(s.transport()?.aborted).toBe(false);
  await rs.advanceTimersByTimeAsync(15_000);
  expect((await pending).value).toEqual({ event: "values", data: finalValues });
  await s.stream.next();
});

test("never settles an unrelated run or an identity superseded during a read", async () => {
  const s = setup("success");
  s.getRun.mockResolvedValueOnce({
    ...id,
    run_id: "another-run",
    status: "success",
  });
  const pending = s.stream.next();
  await rs.advanceTimersByTimeAsync(15_000);
  expect(s.getValues).not.toHaveBeenCalled();
  s.getValues.mockImplementationOnce(async () => {
    s.identity.mockReturnValue({ ...id, run_id: "new-run" });
    return finalValues;
  });
  await rs.advanceTimersByTimeAsync(15_000);
  expect(s.transport()?.aborted).toBe(false);
  s.controller.abort();
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
});

test("no run identity means no speculative status lookup", async () => {
  const s = setup("success");
  s.identity.mockReturnValue(undefined);
  const pending = s.stream.next();
  await rs.advanceTimersByTimeAsync(30_000);
  expect(s.getRun).not.toHaveBeenCalled();
  s.controller.abort();
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
});

test("bounded read timeout retries and never overlaps requests", async () => {
  const s = setup("success");
  s.getRun.mockImplementationOnce(
    (_id, signal) =>
      new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new Error("timeout")), {
          once: true,
        });
      }),
  );
  const pending = s.stream.next();
  await rs.advanceTimersByTimeAsync(23_000);
  expect(s.getRun).toHaveBeenCalledTimes(1);
  expect(s.transport()?.aborted).toBe(false);
  await rs.advanceTimersByTimeAsync(7_000);
  expect((await pending).value).toEqual({ event: "values", data: finalValues });
  await s.stream.next();
});

test("cancellation discards a late terminal read and cleans listeners", async () => {
  const s = setup("success");
  const win = new EventTarget();
  const doc = Object.assign(new EventTarget(), { visibilityState: "visible" });
  rs.stubGlobal("window", win);
  rs.stubGlobal("document", doc);
  let resolve!: (value: typeof finalValues) => void;
  s.getValues.mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const pending = s.stream.next();
  win.dispatchEvent(new Event("focus"));
  await rs.advanceTimersByTimeAsync(0);
  doc.dispatchEvent(new Event("visibilitychange"));
  expect(s.getRun).toHaveBeenCalledTimes(1);
  s.controller.abort();
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  resolve(finalValues);
  await rs.advanceTimersByTimeAsync(30_000);
  win.dispatchEvent(new Event("focus"));
  expect(s.getRun).toHaveBeenCalledTimes(1);
});

test("ordinary events and stream errors preserve their original semantics", async () => {
  const s = setup();
  s.next.mockResolvedValueOnce({
    done: false,
    value: { event: "custom", data: { progress: 1 } },
  });
  s.next.mockRejectedValueOnce(new Error("original stream error"));
  expect((await s.stream.next()).value).toEqual({
    event: "custom",
    data: { progress: 1 },
  });
  await expect(s.stream.next()).rejects.toThrow("original stream error");
  expect(s.getRun).not.toHaveBeenCalled();
  expect(s.close).toHaveBeenCalledTimes(1);
});
