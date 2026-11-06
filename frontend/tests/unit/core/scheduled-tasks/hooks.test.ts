import { beforeEach, describe, expect, it, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

import { fetch } from "@/core/api/fetcher";
import {
  createScheduledTask,
  fetchScheduledTasks,
  fetchWorkNotificationTargets,
  updateScheduledTask,
  type ScheduledTaskPayload,
} from "@/core/scheduled-tasks/api";

const mockedFetch = rs.mocked(fetch);

const SAMPLE_TASK = {
  id: "task-1",
  thread_id: null as string | null,
  context_mode: "fresh_thread_per_run" as const,
  last_thread_id: null as string | null,
  title: "Daily summary",
  prompt: "Summarize thread",
  schedule_type: "cron" as const,
  schedule_spec: { cron: "0 9 * * *" },
  timezone: "UTC",
  status: "enabled" as const,
  next_run_at: "2026-07-02T01:00:00+00:00",
  last_run_at: null,
  last_run_id: null,
  last_error: null,
  run_count: 0,
  created_at: "2026-07-01T00:00:00+00:00",
  updated_at: "2026-07-01T00:00:00+00:00",
};

function jsonResponse(body: unknown, ok = true): Response {
  return {
    ok,
    status: 200,
    statusText: "OK",
    json: async () => body,
  } as Response;
}

function errorResponse(
  detail: string,
  status = 400,
  statusText = "Bad Request",
): Response {
  return {
    ok: false,
    status,
    statusText,
    json: async () => ({ detail }),
  } as Response;
}

describe("scheduled tasks api", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  it("fetchScheduledTasks hits GET /api/scheduled-tasks", async () => {
    mockedFetch.mockResolvedValue(jsonResponse([SAMPLE_TASK]));

    const result = await fetchScheduledTasks();

    expect(mockedFetch).toHaveBeenCalledTimes(1);
    const call = mockedFetch.mock.calls[0];
    expect(call).toBeDefined();
    const url = String(call?.[0] as string);
    expect(url).toContain("/api/scheduled-tasks");
    expect(call?.[1]?.method).toBeUndefined();
    expect(result).toEqual([SAMPLE_TASK]);
  });

  it("loads Work notification targets through the authenticated fetcher", async () => {
    const targets = {
      enabled: true,
      targets: [
        { connection_id: "connection-test", label: "Work private chat" },
      ],
    };
    mockedFetch.mockResolvedValue(jsonResponse(targets));
    const signal = new AbortController().signal;

    await expect(fetchWorkNotificationTargets(signal)).resolves.toEqual(
      targets,
    );
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/scheduled-tasks/work-notification-targets"),
      { signal },
    );
  });

  it("treats an older Gateway without the Work target route as disabled", async () => {
    mockedFetch.mockResolvedValue(errorResponse("Not found", 404));

    await expect(fetchWorkNotificationTargets()).resolves.toEqual({
      enabled: false,
      targets: [],
    });
  });

  it("preserves omission and explicit removal when updating Work notifications", async () => {
    mockedFetch.mockResolvedValue(jsonResponse(SAMPLE_TASK));

    await updateScheduledTask("task-1", { title: "Renamed" });
    await updateScheduledTask("task-1", { work_notification: null });
    await updateScheduledTask("task-1", {
      work_notification: { connection_id: "connection-test" },
    });

    expect(
      mockedFetch.mock.calls.map((call) => JSON.parse(call[1]?.body as string)),
    ).toEqual([
      { title: "Renamed" },
      { work_notification: null },
      { work_notification: { connection_id: "connection-test" } },
    ]);
  });

  it("createScheduledTask hits POST /api/scheduled-tasks with payload", async () => {
    mockedFetch.mockResolvedValue(jsonResponse(SAMPLE_TASK));

    const payload: ScheduledTaskPayload = {
      context_mode: "fresh_thread_per_run",
      thread_id: null,
      title: "Daily summary",
      prompt: "Summarize thread",
      schedule_type: "cron",
      schedule_spec: { cron: "0 9 * * *" },
      timezone: "UTC",
    };
    const result = await createScheduledTask(payload);

    expect(mockedFetch).toHaveBeenCalledTimes(1);
    const call = mockedFetch.mock.calls[0];
    expect(call).toBeDefined();
    const url = String(call?.[0] as string);
    expect(url).toContain("/api/scheduled-tasks");
    expect(call?.[1]?.method).toBe("POST");
    const body = call?.[1]?.body as string;
    expect(JSON.parse(body)).toEqual(payload);
    expect(result).toEqual(SAMPLE_TASK);
  });

  it("throws an Error carrying backend detail on failure", async () => {
    mockedFetch.mockResolvedValue(
      errorResponse("Cron expression is invalid", 422, "Unprocessable Entity"),
    );

    await expect(fetchScheduledTasks()).rejects.toThrow(
      "Cron expression is invalid",
    );
  });

  it("falls back to a generic message when detail is missing", async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      // body is not valid JSON → body.detail is undefined → fallback used
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    } as unknown as Response);

    await expect(fetchScheduledTasks()).rejects.toThrow(/Failed to load/);
  });
});
