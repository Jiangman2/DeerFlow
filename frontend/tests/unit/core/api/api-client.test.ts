import { afterEach, expect, test, rs } from "@rstest/core";

import {
  clearReconnectRun,
  getAPIClient,
  isInactiveRunStreamError,
  isRunNotCancellableError,
} from "@/core/api/api-client";
import {
  inheritPptMasterRuntimeThread,
  rememberPptMasterRuntimeThread,
} from "@/core/api/ppt-master-runtime";

function makeSessionStorage() {
  const values = new Map<string, string>();
  return {
    getItem: rs.fn((key: string) => values.get(key) ?? null),
    removeItem: rs.fn((key: string) => {
      values.delete(key);
    }),
    setItem: rs.fn((key: string, value: string) => {
      values.set(key, value);
    }),
  };
}

afterEach(() => {
  rs.unstubAllGlobals();
});

test("identifies inactive run stream errors", () => {
  const error = Object.assign(
    new Error(
      'HTTP 409: {"detail":"Run run-1 is not active on this worker and cannot be streamed"}',
    ),
    { status: 409 },
  );

  expect(isInactiveRunStreamError(error)).toBe(true);
});

test("does not classify unrelated conflict errors as inactive streams", () => {
  const error = Object.assign(new Error("HTTP 409: run is still active"), {
    status: 409,
  });

  expect(isInactiveRunStreamError(error)).toBe(false);
});

test("clears matching reconnect metadata", () => {
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem("lg:stream:thread-1", "run-1");
  rs.stubGlobal("window", { sessionStorage });

  clearReconnectRun("thread-1", "run-1");

  expect(sessionStorage.removeItem).toHaveBeenCalledWith("lg:stream:thread-1");
});

test("keeps newer reconnect metadata", () => {
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem("lg:stream:thread-1", "newer-run");
  rs.stubGlobal("window", { sessionStorage });

  clearReconnectRun("thread-1", "stale-run");

  expect(sessionStorage.removeItem).not.toHaveBeenCalled();
});

test("ignores reconnect metadata storage access failures", () => {
  rs.stubGlobal("window", {
    get sessionStorage() {
      throw new DOMException("Blocked", "SecurityError");
    },
  });

  expect(() => clearReconnectRun("thread-1", "run-1")).not.toThrow();
});

test("clears stale reconnect metadata when join stream cannot be resumed", async () => {
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem("lg:stream:thread-1", "run-1");
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal(
    "fetch",
    rs.fn(async () => {
      return new Response(
        JSON.stringify({
          detail:
            "Run run-1 is not active on this worker and cannot be streamed",
        }),
        { status: 409 },
      );
    }),
  );

  await expect(
    getAPIClient(true).runs.joinStream("thread-1", "run-1").next(),
  ).resolves.toMatchObject({ done: true });

  expect(sessionStorage.removeItem).toHaveBeenCalledWith("lg:stream:thread-1");
});

test("rethrows unrelated streaming errors", async () => {
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem("lg:stream:thread-1", "run-1");
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal(
    "fetch",
    rs.fn(async () => {
      return new Response(JSON.stringify({ detail: "run is still active" }), {
        status: 409,
      });
    }),
  );

  await expect(
    getAPIClient(true).runs.joinStream("thread-1", "run-1").next(),
  ).rejects.toThrow("HTTP 409");

  expect(sessionStorage.removeItem).not.toHaveBeenCalled();
});

test("identifies terminal-state cancel conflicts", () => {
  const error = Object.assign(
    new Error(
      'HTTP 409: {"detail":"Run run-1 is not cancellable (status: success)"}',
    ),
    { status: 409 },
  );

  expect(isRunNotCancellableError(error)).toBe(true);
});

test("does not classify not-active-on-worker cancel as terminal", () => {
  // A run still pending/running on another worker is a real cancel failure —
  // it must stay visible and must NOT be swallowed.
  const error = Object.assign(
    new Error(
      'HTTP 409: {"detail":"Run run-1 is not active on this worker and cannot be cancelled"}',
    ),
    { status: 409 },
  );

  expect(isRunNotCancellableError(error)).toBe(false);
});

test("swallows terminal-state cancel 409 and clears stale key", async () => {
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem("lg:stream:thread-1", "run-1");
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal(
    "fetch",
    rs.fn(async () => {
      return new Response(
        JSON.stringify({
          detail: "Run run-1 is not cancellable (status: success)",
        }),
        { status: 409 },
      );
    }),
  );

  // Resolves (no throw) — cancelling an already-finished run is a no-op.
  await expect(
    getAPIClient(true).runs.cancel("thread-1", "run-1"),
  ).resolves.toBeUndefined();

  expect(sessionStorage.removeItem).toHaveBeenCalledWith("lg:stream:thread-1");
});

test("rethrows not-active-on-worker cancel 409", async () => {
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem("lg:stream:thread-1", "run-1");
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal(
    "fetch",
    rs.fn(async () => {
      return new Response(
        JSON.stringify({
          detail:
            "Run run-1 is not active on this worker and cannot be cancelled",
        }),
        { status: 409 },
      );
    }),
  );

  await expect(
    getAPIClient(true).runs.cancel("thread-1", "run-1"),
  ).rejects.toThrow("HTTP 409");

  expect(sessionStorage.removeItem).not.toHaveBeenCalled();
});

test("short-circuits reconnect to a terminal run", async () => {
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem("lg:stream:thread-1", "run-1");
  const fetchFn = rs.fn(async (url: string | URL) => {
    const path = url.toString();
    // Preflight GET /threads/{tid}/runs/{runId} reports a finished run.
    if (path.endsWith("/runs/run-1")) {
      return new Response(JSON.stringify({ status: "success" }), {
        status: 200,
      });
    }
    // If join were attempted it must never run; fail loudly if it does.
    return new Response(JSON.stringify({ detail: "unexpected join" }), {
      status: 500,
    });
  });
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal("fetch", fetchFn);

  const gen = getAPIClient(true).runs.joinStream("thread-1", "run-1");
  await expect(gen.next()).resolves.toMatchObject({ done: true });

  // Preflight only — no stream/join request beyond the GET.
  expect(fetchFn).toHaveBeenCalledTimes(1);
  expect(sessionStorage.removeItem).toHaveBeenCalledWith("lg:stream:thread-1");
});

test("hydrates active run input before replaying incremental events", async () => {
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem("lg:stream:thread-input", "run-input");
  const fetchFn = rs.fn(async (url: string | URL) => {
    const path = new URL(url.toString()).pathname;
    if (path.endsWith("/runs/run-input")) {
      return new Response(
        JSON.stringify({
          status: "running",
          kwargs: {
            input: {
              messages: [
                { id: "human-2", type: "human", content: "Second question" },
              ],
            },
          },
        }),
        { status: 200 },
      );
    }
    if (path.endsWith("/threads/thread-input/state")) {
      return new Response(
        JSON.stringify({
          values: {
            messages: [
              { id: "human-1", type: "human", content: "First question" },
              { id: "human-2", type: "human", content: "Second question" },
            ],
          },
        }),
        { status: 200 },
      );
    }
    return new Response(
      JSON.stringify({
        detail:
          "Run run-input is not active on this worker and cannot be streamed",
      }),
      { status: 409 },
    );
  });
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal("fetch", fetchFn);

  const stream = getAPIClient(true).runs.joinStream(
    "thread-input",
    "run-input",
  );
  await expect(stream.next()).resolves.toMatchObject({
    value: {
      event: "values",
      data: {
        messages: [
          { id: "human-1", content: "First question" },
          { id: "human-2", content: "Second question" },
        ],
      },
    },
    done: false,
  });
  await expect(stream.next()).resolves.toMatchObject({ done: true });

  expect(fetchFn).toHaveBeenCalledTimes(3);
  expect(sessionStorage.removeItem).toHaveBeenCalledWith(
    "lg:stream:thread-input",
  );
});

test("continues reconnect when input hydration state is unavailable", async () => {
  const fetchFn = rs.fn(async (url: string | URL) => {
    const path = new URL(url.toString()).pathname;
    if (path.endsWith("/runs/run-no-state")) {
      return new Response(
        JSON.stringify({
          status: "running",
          kwargs: {
            input: {
              messages: [{ id: "human-2", type: "human", content: "Second" }],
            },
          },
        }),
        { status: 200 },
      );
    }
    if (path.endsWith("/threads/thread-no-state/state")) {
      return new Response(JSON.stringify({ detail: "state unavailable" }), {
        status: 404,
      });
    }
    return new Response(
      JSON.stringify({
        detail:
          "Run run-no-state is not active on this worker and cannot be streamed",
      }),
      { status: 409 },
    );
  });
  rs.stubGlobal("fetch", fetchFn);

  await expect(
    getAPIClient(true)
      .runs.joinStream("thread-no-state", "run-no-state")
      .next(),
  ).resolves.toMatchObject({ done: true });

  expect(fetchFn).toHaveBeenCalledTimes(3);
});

test("falls back to join when preflight cannot resolve the run", async () => {
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem("lg:stream:thread-1", "run-1");
  const fetchFn = rs.fn(async (url: string | URL) => {
    const path = url.toString();
    // Preflight GET 404s (record evicted) — must fall back to join.
    if (path.endsWith("/runs/run-1")) {
      return new Response(JSON.stringify({ detail: "Run run-1 not found" }), {
        status: 404,
      });
    }
    // Join then surfaces the inactive-stream 409 and clears the key.
    return new Response(
      JSON.stringify({
        detail: "Run run-1 is not active on this worker and cannot be streamed",
      }),
      { status: 409 },
    );
  });
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal("fetch", fetchFn);

  await expect(
    getAPIClient(true).runs.joinStream("thread-1", "run-1").next(),
  ).resolves.toMatchObject({ done: true });

  expect(sessionStorage.removeItem).toHaveBeenCalledWith("lg:stream:thread-1");
});

test("proceeds to join when the run is still active", async () => {
  // Positive path: a running/pending run must NOT be short-circuited — the
  // preflight must let the real join through so an in-flight stream can be
  // rejoined. Proves the guard does not over-eagerly skip active runs.
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem("lg:stream:thread-1", "run-1");
  const fetchFn = rs.fn(async (url: string | URL) => {
    const path = url.toString();
    // Preflight GET reports an active run.
    if (path.endsWith("/runs/run-1")) {
      return new Response(JSON.stringify({ status: "running" }), {
        status: 200,
      });
    }
    // The real join is attempted (here it surfaces the inactive-stream 409,
    // which the wrapper catches and clears the key — same path as production).
    return new Response(
      JSON.stringify({
        detail: "Run run-1 is not active on this worker and cannot be streamed",
      }),
      { status: 409 },
    );
  });
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal("fetch", fetchFn);

  await expect(
    getAPIClient(true).runs.joinStream("thread-1", "run-1").next(),
  ).resolves.toMatchObject({ done: true });

  // With no input in the run record, hydration requires no state read: one
  // preflight GET plus the real join. A short-circuit would be one request.
  expect(fetchFn).toHaveBeenCalledTimes(2);
  expect(sessionStorage.removeItem).toHaveBeenCalledWith("lg:stream:thread-1");
});

test("short-circuits reconnect to an interrupted (user-cancelled) run", async () => {
  // Regression: interrupted is a persisted terminal status written by
  // RunManager.cancel(). Reconnecting to it must short-circuit like other
  // terminal states, otherwise — once the bridge is reaped — joinStream blocks
  // forever and isLoading sticks. Keeps the frontend status set aligned with
  // the backend RunStatus contract.
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem("lg:stream:thread-1", "run-1");
  const fetchFn = rs.fn(async (url: string | URL) => {
    const path = url.toString();
    if (path.endsWith("/runs/run-1")) {
      return new Response(JSON.stringify({ status: "interrupted" }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify({ detail: "unexpected join" }), {
      status: 500,
    });
  });
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal("fetch", fetchFn);

  const gen = getAPIClient(true).runs.joinStream("thread-1", "run-1");
  await expect(gen.next()).resolves.toMatchObject({ done: true });

  expect(fetchFn).toHaveBeenCalledTimes(1);
  expect(sessionStorage.removeItem).toHaveBeenCalledWith("lg:stream:thread-1");
});

test("reads ppt-master checkpoint state from the isolated runtime", async () => {
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem(
    "deerflow:ppt-master-runtime:thread-ppt",
    "ppt-master-runner",
  );
  const fetchFn = rs.fn(async (url: string | URL) => {
    const path = url.toString();
    if (path.includes("/api/ppt-master-runtime/threads/thread-ppt/state")) {
      return new Response(
        JSON.stringify({ values: { messages: [] }, next: [], tasks: [] }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ detail: "wrong runtime" }), {
      status: 404,
    });
  });
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal("fetch", fetchFn);

  await expect(
    getAPIClient().threads.getState("thread-ppt"),
  ).resolves.toMatchObject({ values: { messages: [] } });

  expect(fetchFn).toHaveBeenCalledTimes(1);
  expect(String(fetchFn.mock.calls[0]?.[0])).toContain(
    "/api/ppt-master-runtime/threads/thread-ppt/state",
  );
});

test("hydrates ppt-master reconnect input from the isolated runtime", async () => {
  const sessionStorage = makeSessionStorage();
  sessionStorage.setItem(
    "deerflow:ppt-master-runtime:thread-ppt-reconnect",
    "ppt-master-runner",
  );
  const fetchFn = rs.fn(async (url: string | URL) => {
    const path = url.toString();
    if (
      path.includes(
        "/api/ppt-master-runtime/threads/thread-ppt-reconnect/runs/run-ppt-input/stream",
      )
    ) {
      return new Response(
        JSON.stringify({
          detail:
            "Run run-ppt-input is not active on this worker and cannot be streamed",
        }),
        { status: 409 },
      );
    }
    if (
      path.includes(
        "/api/ppt-master-runtime/threads/thread-ppt-reconnect/runs/run-ppt-input",
      )
    ) {
      return new Response(
        JSON.stringify({
          status: "running",
          kwargs: {
            input: {
              messages: [
                { id: "ppt-human", type: "human", content: "/ppt-master" },
              ],
            },
          },
        }),
        { status: 200 },
      );
    }
    if (
      path.includes(
        "/api/ppt-master-runtime/threads/thread-ppt-reconnect/state",
      )
    ) {
      return new Response(
        JSON.stringify({ values: { messages: [] }, next: [], tasks: [] }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ detail: "wrong runtime" }), {
      status: 404,
    });
  });
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal("fetch", fetchFn);

  const stream = getAPIClient().runs.joinStream(
    "thread-ppt-reconnect",
    "run-ppt-input",
  );
  await expect(stream.next()).resolves.toMatchObject({
    value: {
      event: "values",
      data: {
        messages: [{ id: "ppt-human", content: "/ppt-master" }],
      },
    },
    done: false,
  });
  await expect(stream.next()).resolves.toMatchObject({ done: true });

  expect(fetchFn).toHaveBeenCalledTimes(3);
  for (const call of fetchFn.mock.calls) {
    expect(String(call[0])).toContain("/api/ppt-master-runtime/");
  }
});

test("routes an authoritative runner thread id without ordinary 404 probes", async () => {
  const sessionStorage = makeSessionStorage();
  const fetchFn = rs.fn(async (url: string | URL) => {
    const path = url.toString();
    if (
      path.includes("/api/ppt-master-runtime/threads/runner-thread-ppt/state")
    ) {
      return new Response(
        JSON.stringify({ values: { messages: [] }, next: [], tasks: [] }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ detail: "wrong runtime" }), {
      status: 404,
    });
  });
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal("fetch", fetchFn);

  rememberPptMasterRuntimeThread("welcome-thread-ppt", {
    messages: [{ role: "user", content: "/ppt-master 创建演示文稿" }],
  });
  expect(
    inheritPptMasterRuntimeThread("welcome-thread-ppt", "runner-thread-ppt"),
  ).toBe(true);

  await expect(
    getAPIClient().threads.getState("runner-thread-ppt"),
  ).resolves.toMatchObject({ values: { messages: [] } });

  expect(fetchFn).toHaveBeenCalledTimes(1);
  expect(String(fetchFn.mock.calls[0]?.[0])).toContain(
    "/api/ppt-master-runtime/threads/runner-thread-ppt/state",
  );
});

test("recovers ppt-master routing after a full refresh without 404 probes", async () => {
  const sessionStorage = makeSessionStorage();
  const fetchFn = rs.fn(async (url: string | URL) => {
    const path = url.toString();
    if (path.includes("/api/langgraph/threads/refreshed-ppt/runs")) {
      return new Response(
        JSON.stringify([
          {
            run_id: "run-ppt",
            thread_id: "refreshed-ppt",
            assistant_id: "ppt-master-runner",
            status: "success",
            metadata: {},
            kwargs: {},
            multitask_strategy: "reject",
            created_at: "",
            updated_at: "",
          },
        ]),
        { status: 200 },
      );
    }
    if (path.includes("/api/ppt-master-runtime/threads/refreshed-ppt/state")) {
      return new Response(
        JSON.stringify({ values: { messages: [] }, next: [], tasks: [] }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ detail: "wrong runtime" }), {
      status: 404,
    });
  });
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal("fetch", fetchFn);

  await expect(
    getAPIClient().threads.getState("refreshed-ppt"),
  ).resolves.toMatchObject({ values: { messages: [] } });

  const requestedURLs = fetchFn.mock.calls.map((call) => String(call[0]));
  expect(requestedURLs).toHaveLength(2);
  expect(requestedURLs).toEqual(
    expect.arrayContaining([
      expect.stringContaining("/api/langgraph/threads/refreshed-ppt/runs"),
      expect.stringContaining(
        "/api/ppt-master-runtime/threads/refreshed-ppt/state",
      ),
    ]),
  );
  expect(requestedURLs).not.toContain(
    "http://localhost:2026/api/langgraph/threads/refreshed-ppt",
  );
  expect(requestedURLs).not.toContain(
    "http://localhost:2026/api/langgraph/threads/refreshed-ppt/state",
  );
});

test("routes refreshed ppt-master thread metadata to the isolated runtime", async () => {
  const sessionStorage = makeSessionStorage();
  const fetchFn = rs.fn(async (url: string | URL) => {
    const path = url.toString();
    if (path.includes("/api/langgraph/threads/refreshed-ppt-meta/runs")) {
      return new Response(
        JSON.stringify([
          {
            run_id: "run-ppt-meta",
            thread_id: "refreshed-ppt-meta",
            assistant_id: "ppt-master-runner",
            status: "success",
            metadata: {},
            kwargs: {},
            multitask_strategy: "reject",
            created_at: "",
            updated_at: "",
          },
        ]),
        { status: 200 },
      );
    }
    if (path.endsWith("/api/ppt-master-runtime/threads/refreshed-ppt-meta")) {
      return new Response(
        JSON.stringify({
          thread_id: "refreshed-ppt-meta",
          status: "idle",
          metadata: { runtime_profile: "ppt-master-runner" },
          values: {},
        }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ detail: "wrong runtime" }), {
      status: 404,
    });
  });
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal("fetch", fetchFn);

  await expect(
    getAPIClient().threads.get("refreshed-ppt-meta"),
  ).resolves.toMatchObject({
    thread_id: "refreshed-ppt-meta",
    metadata: { runtime_profile: "ppt-master-runner" },
  });

  const requestedURLs = fetchFn.mock.calls.map((call) => String(call[0]));
  expect(requestedURLs).toHaveLength(2);
  expect(requestedURLs[1]).toContain(
    "/api/ppt-master-runtime/threads/refreshed-ppt-meta",
  );
});
