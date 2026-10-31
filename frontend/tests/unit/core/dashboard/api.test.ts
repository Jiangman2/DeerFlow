import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "/backend",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  getDashboardFailures,
  getDashboardKnowledge,
  getDashboardRunRankings,
  getDashboardSummary,
  getDashboardUsers,
} from "@/core/dashboard/api";

const mockedFetch = rs.mocked(fetcher);

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("dashboard failures api", () => {
  test("loads one page from the all-failures endpoint", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          total: 8,
          items: [
            {
              run_id: "run-8",
              thread_id: "thread-8",
              can_open: true,
              chat_url: "/workspace/chats/thread-8?admin_review=1",
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(getDashboardFailures(5, 5)).resolves.toMatchObject({
      total: 8,
      items: [
        {
          run_id: "run-8",
          can_open: true,
          chat_url: "/workspace/chats/thread-8?admin_review=1",
        },
      ],
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      "/backend/api/admin/dashboard/failures?limit=5&offset=5",
    );
  });

  test("filters failed runs by account or display name", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ total: 1, items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await getDashboardFailures(5, 0, "张 三");

    expect(mockedFetch).toHaveBeenCalledWith(
      "/backend/api/admin/dashboard/failures?limit=5&offset=0&q=%E5%BC%A0+%E4%B8%89",
    );
  });
});

describe("dashboard progressive summary api", () => {
  test("does not expose an upstream HTML error page to users", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response("<html><h1>502 Bad Gateway</h1></html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    );

    await expect(getDashboardSummary({ hours: 24 })).rejects.toThrow(
      "服务暂时不可用（HTTP 502），请稍后重试",
    );
  });

  test("keeps heavy knowledge and event analytics out of the headline request", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ overview: {} }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await getDashboardSummary({ hours: 720 });

    expect(mockedFetch).toHaveBeenCalledWith(
      "/backend/api/admin/dashboard/summary?hours=720&limit=10&include_knowledge=false&include_business_details=false",
      { signal: expect.any(AbortSignal) },
    );
  });

  test("cancels a superseded range without retrying", async () => {
    const controller = new AbortController();
    mockedFetch.mockImplementationOnce(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    const pending = getDashboardSummary({ hours: 720 }, controller.signal);
    controller.abort();
    await expect(pending).rejects.toThrow("Aborted");
    expect(mockedFetch).toHaveBeenCalledTimes(1);
  });

  test("ends an unresponsive summary with a recoverable timeout", async () => {
    rs.useFakeTimers();
    try {
      mockedFetch.mockImplementationOnce(
        (_url, init) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      );
      const result = expect(
        getDashboardSummary({ hours: 720 }),
      ).rejects.toThrow("统计加载超时");
      await rs.advanceTimersByTimeAsync(60_000);
      await result;
      expect(mockedFetch).toHaveBeenCalledTimes(1);
    } finally {
      rs.useRealTimers();
    }
  });

  test("loads WeKnora analytics through the independent knowledge endpoint", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ available: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await getDashboardKnowledge({
      hours: 48,
      start_at: "2026-09-01T00:00:00Z",
      end_at: "2026-09-03T00:00:00Z",
      status: "success",
    });

    expect(mockedFetch).toHaveBeenCalledWith(
      "/backend/api/admin/dashboard/knowledge?hours=48&limit=10&start_at=2026-09-01T00%3A00%3A00Z&end_at=2026-09-03T00%3A00%3A00Z",
    );
  });

  test("loads slow and high-token cards from the bounded ranking endpoint", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ slow_runs: [], high_token_runs: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await getDashboardRunRankings({
      hours: 720,
      start_at: "2026-08-01T00:00:00Z",
      end_at: "2026-09-01T00:00:00Z",
    });

    expect(mockedFetch).toHaveBeenCalledWith(
      "/backend/api/admin/dashboard/run-rankings?hours=720&limit=10&start_at=2026-08-01T00%3A00%3A00Z&end_at=2026-09-01T00%3A00%3A00Z",
    );
  });
});

describe("registered dashboard users api", () => {
  test("sends the server-side identity search with pagination", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ total: 1, items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await getDashboardUsers(5, 10, "张 三");

    expect(mockedFetch).toHaveBeenCalledWith(
      "/backend/api/admin/dashboard/users?limit=5&offset=10&q=%E5%BC%A0+%E4%B8%89",
    );
  });
});
