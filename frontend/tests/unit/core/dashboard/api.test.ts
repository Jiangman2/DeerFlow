import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "/backend",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import { getDashboardFailures, getDashboardUsers } from "@/core/dashboard/api";

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
