import { afterEach, expect, rs, test } from "@rstest/core";

import {
  fetchConfirmationState,
  openConfirmationPage,
  stableConfirmationEntry,
} from "@/core/ppt-confirmation/api";

afterEach(() => {
  rs.unstubAllGlobals();
});

const sample = {
  protocol: "ppt-confirmation-v1",
  thread_id: "thread",
  task_id: "task",
  admitted: true,
  auto_enabled: true,
  cancelled: false,
  run_active: true,
  can_open: true,
  gate: {
    state: "waiting_user",
    stage: "stage1",
    due_at: 1060,
    auto_block_reason: null,
  },
};

function reply(value: unknown) {
  const fetch = rs.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(value), {
        headers: { "content-type": "application/json" },
      }),
  );
  rs.stubGlobal("fetch", fetch);
  return fetch;
}

test("durable state is an authenticated read, not a confirmation POST", async () => {
  const fetch = reply(sample);
  expect(
    await fetchConfirmationState(
      "thread",
      "task",
      new AbortController().signal,
    ),
  ).toEqual(sample);
  const init = fetch.mock.calls[0]![1]!;
  expect(init.credentials).toBe("include");
  expect(init.redirect).toBe("error");
  expect(init.cache).toBe("no-store");
  expect(init.method).toBeUndefined();
});

test.each([
  { ...sample, thread_id: "other" },
  { ...sample, task_id: "other" },
  { ...sample, protocol: "future" },
  { ...sample, auto_enabled: "true" },
  { ...sample, gate: { ...sample.gate, stage: "invented" } },
])("foreign or malformed state fails closed %j", async (value) => {
  reply(value);
  await expect(
    fetchConfirmationState("thread", "task", new AbortController().signal),
  ).rejects.toThrow();
});

test("cancelled history has no actionable gate", async () => {
  reply({
    ...sample,
    cancelled: true,
    run_active: false,
    can_open: false,
    gate: null,
  });
  expect(
    (
      await fetchConfirmationState(
        "thread",
        "task",
        new AbortController().signal,
      )
    ).gate,
  ).toBeNull();
});

test("response limit is enforced", async () => {
  reply({ ...sample, unexpected: "x".repeat(17000) });
  await expect(
    fetchConfirmationState("thread", "task", new AbortController().signal),
  ).rejects.toThrow("confirmation_response_limit");
});

test("manual takeover carries CSRF and accepts only the owned bounded proxy", async () => {
  rs.stubGlobal("document", { cookie: "csrf_token=fixture-only" });
  const fetch = reply({
    url: "/api/threads/thread/live-preview/5050/",
    auto_enabled: false,
    confirmation_submitted: false,
    agent_started: false,
  });
  expect(await openConfirmationPage("thread", "task")).toBe(
    "/api/threads/thread/live-preview/5050/",
  );
  const init = fetch.mock.calls[0]![1]!;
  expect(init.method).toBe("POST");
  expect(init.body).toBe("{}");
  expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("fixture-only");
});

test.each([
  "http://127.0.0.1:5050/",
  "https://example.invalid/",
  "/api/threads/other/live-preview/5050/",
  "/api/threads/thread/live-preview/6060/",
])("untrusted redirect %s is rejected", async (url) => {
  reply({
    url,
    auto_enabled: false,
    confirmation_submitted: false,
    agent_started: false,
  });
  await expect(openConfirmationPage("thread", "task")).rejects.toThrow(
    "invalid_confirmation_open",
  );
});

test("entry identities cannot inject paths", () => {
  expect(stableConfirmationEntry("thread", "task")).toBe(
    "/workspace/ppt-confirmation/thread/task",
  );
  expect(() => stableConfirmationEntry("../other", "task")).toThrow();
});
