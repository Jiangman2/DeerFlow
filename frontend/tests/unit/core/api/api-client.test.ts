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
import { preparePublicPptRequest } from "@/core/skills/public-ppt-request";

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

test("new /ppt-master submits the public Skill to the ordinary Agent with its selected model", async () => {
  const requests: { url: string; body: unknown }[] = [];
  const sessionStorage = makeSessionStorage();
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage,
  });
  rs.stubGlobal(
    "fetch",
    rs.fn(async (url: string | URL, init?: RequestInit) => {
      requests.push({
        url: String(url),
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      });
      if (new URL(String(url)).pathname.endsWith("/runs"))
        return Response.json([]);
      if (new URL(String(url)).pathname.endsWith("/state"))
        return Response.json({ values: {} });
      return new Response("event: end\ndata: {}\n\n", {
        headers: { "content-type": "text/event-stream" },
      });
    }),
  );
  const input = {
    messages: [{ role: "user" as const, content: "/ppt-master 制作汇报" }],
  };
  for await (const _ of getAPIClient().runs.stream(
    "new-public-ppt-skill",
    "lead_agent",
    { input, context: { model_name: "glm-5.3-flash" }, streamMode: ["values"] },
  )) {
    void _; // Drain the SDK stream to exercise its routing.
  }
  expect(requests.map((r) => r.url)).toEqual([
    expect.stringContaining("/api/langgraph/threads/new-public-ppt-skill/runs"),
    expect.stringContaining(
      "/api/langgraph/threads/new-public-ppt-skill/state",
    ),
    expect.stringContaining(
      "/api/langgraph/threads/new-public-ppt-skill/runs/stream",
    ),
  ]);
  expect(requests[2]?.body).toMatchObject({
    assistant_id: "lead_agent",
    input: preparePublicPptRequest({ input }).input,
    context: { model_name: "glm-5.3-flash" },
  });
  expect(JSON.stringify(requests[2]?.body)).not.toContain(
    "ppt_confirmation_mode",
  );
  expect(JSON.stringify(requests[2]?.body)).not.toContain("ppt-master-runner");
  expect(sessionStorage.setItem).not.toHaveBeenCalled();
});

for (const status of ["blocked", "completed", "lookup-failed", "new-thread"]) {
  test(`ordinary PPT shortcut handles checkpoint state ${status} without changing it`, async () => {
    const requests: { url: string; method: string; body: unknown }[] = [];
    rs.stubGlobal("window", {
      location: { origin: "http://localhost:2026" },
      sessionStorage: makeSessionStorage(),
    });
    rs.stubGlobal(
      "fetch",
      rs.fn(async (url: string | URL, init?: RequestInit) => {
        const path = new URL(String(url)).pathname;
        requests.push({
          url: path,
          method: init?.method ?? "GET",
          body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
        });
        if (path.endsWith("/runs")) return Response.json([]);
        if (path.endsWith("/state")) {
          if (status === "lookup-failed")
            return Response.json({}, { status: 403 });
          if (status === "new-thread")
            return Response.json({}, { status: 404 });
          return Response.json({
            values: { skill_workflow: { status, skill_name: "ppt-master" } },
          });
        }
        return new Response("event: end\ndata: {}\n\n", {
          headers: { "content-type": "text/event-stream" },
        });
      }),
    );
    const stream = getAPIClient().runs.stream(
      `ordinary-ppt-checkpoint-${status}`,
      "lead_agent",
      {
        input: {
          messages: [{ role: "user", content: "/ppt-master 制作汇报" }],
        },
      },
    );
    if (status === "new-thread") {
      for await (const event of stream) void event;
      expect(requests.filter((r) => r.method === "POST")).toHaveLength(1);
      expect(JSON.stringify(requests.at(-1)?.body)).not.toContain(
        '"skill_workflow"',
      );
    } else {
      await expect(stream.next()).rejects.toThrow();
      expect(requests.every((r) => r.method === "GET")).toBe(true);
    }
  });
}

test("old Runner receives the original slash request without the ordinary-input adapter", async () => {
  const requests: { url: string; body: unknown }[] = [];
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage: makeSessionStorage(),
  });
  rs.stubGlobal(
    "fetch",
    rs.fn(async (url: string | URL, init?: RequestInit) => {
      const path = new URL(String(url)).pathname;
      requests.push({
        url: path,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      });
      if (path.endsWith("/runs"))
        return Response.json([{ assistant_id: "ppt-master-runner" }]);
      return new Response("event: end\ndata: {}\n\n", {
        headers: { "content-type": "text/event-stream" },
      });
    }),
  );
  const input = {
    messages: [{ role: "user" as const, content: "/ppt-master 继续旧任务" }],
  };
  for await (const event of getAPIClient().runs.stream(
    "old-runner-slash-unchanged",
    "lead_agent",
    { input },
  ))
    void event;
  expect(requests).toHaveLength(2);
  expect(requests[1]?.url).toContain("/api/ppt-master-runtime/");
  expect(requests[1]?.body).toMatchObject({ input });
});

for (const operation of [
  "continue",
  "history",
  "cancel",
  "reconnect",
] as const) {
  test(`retains the old Runner owner for ${operation} before hydration with storage blocked`, async () => {
    const threadId = `legacy-runner-${operation}-no-storage`;
    const requests: { url: string; body: unknown }[] = [];
    rs.stubGlobal("window", {
      location: { origin: "http://localhost:2026" },
      get sessionStorage() {
        throw new DOMException("Blocked", "SecurityError");
      },
    });
    rs.stubGlobal(
      "fetch",
      rs.fn(async (url: string | URL, init?: RequestInit) => {
        const path = new URL(String(url)).pathname;
        requests.push({
          url: String(url),
          body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
        });
        if (path.endsWith("/runs"))
          return Response.json([{ assistant_id: "ppt-master-runner" }]);
        if (path.endsWith("/runs/old-run"))
          return Response.json({ status: "running" });
        if (path.endsWith("/history")) return Response.json([]);
        if (path.endsWith("/cancel")) return Response.json({});
        return new Response("event: end\ndata: {}\n\n", {
          headers: { "content-type": "text/event-stream" },
        });
      }),
    );
    const client = getAPIClient();
    if (operation === "continue") {
      for await (const _ of client.runs.stream(threadId, "lead_agent", {
        input: { messages: [{ role: "user", content: "继续" }] },
      })) {
        void _;
      }
      expect(requests[1]?.body).toMatchObject({
        assistant_id: "ppt-master-runner",
        metadata: { runtime_profile: "ppt-master-runner" },
      });
    } else if (operation === "history")
      await client.threads.getHistory(threadId);
    else if (operation === "cancel")
      await client.runs.cancel(threadId, "old-run");
    else
      for await (const _ of client.runs.joinStream(threadId, "old-run")) {
        void _;
      }
    expect(requests[0]?.url).toContain(
      `/api/langgraph/threads/${threadId}/runs`,
    );
    expect(requests.length).toBeGreaterThan(1);
    expect(
      requests
        .slice(1)
        .every((r) =>
          r.url.includes(`/api/ppt-master-runtime/threads/${threadId}`),
        ),
    ).toBe(true);
  });
}

test("failed owner lookup never dispatches a continuation to the ordinary Agent", async () => {
  rs.stubGlobal("window", {
    location: { origin: "http://localhost:2026" },
    sessionStorage: makeSessionStorage(),
  });
  const fetchFn = rs.fn(async () =>
    Response.json({ detail: "forbidden" }, { status: 403 }),
  );
  rs.stubGlobal("fetch", fetchFn);
  await expect(
    getAPIClient()
      .runs.stream("unknown-ppt-owner", "lead_agent", {
        input: { messages: [{ role: "user", content: "继续" }] },
      })
      .next(),
  ).rejects.toThrow();
  expect(fetchFn).toHaveBeenCalledTimes(1);
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

  // Two requests: preflight GET + the real join. A short-circuit would be one.
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
    metadata: { runtime_profile: "ppt-master-runner" },
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
