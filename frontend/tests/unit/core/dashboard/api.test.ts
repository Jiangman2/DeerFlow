import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "/backend",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import { getDashboardFailures } from "@/core/dashboard/api";

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
});
