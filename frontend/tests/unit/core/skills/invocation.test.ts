import { beforeEach, expect, rs, test } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));

import { fetch as fetcher } from "@/core/api/fetcher";
import { loadEmployeeInvocationSkills } from "@/core/skills/invocation";

beforeEach(() => {
  rs.mocked(fetcher).mockReset();
});

test("employee discovery forwards exact source/thread and has no source fields", async () => {
  rs.mocked(fetcher).mockResolvedValue(
    new Response(
      JSON.stringify({
        revision: "frozen",
        skills: [
          {
            name: "analysis",
            description: "",
            enabled: true,
            source: "should-not-forward",
          },
        ],
      }),
    ),
  );
  const result = await loadEmployeeInvocationSkills({
    name: "周报",
    source: "team",
    threadId: "bob-thread",
  });
  expect(rs.mocked(fetcher).mock.calls[0]?.[0]).toBe(
    "/api/agents/%E5%91%A8%E6%8A%A5/invocation-skills?source=team&thread_id=bob-thread",
  );
  expect(result).toEqual([
    { name: "analysis", description: "", enabled: true },
  ]);
});

test("revoked employee does not fall back to the personal catalog", async () => {
  rs.mocked(fetcher).mockResolvedValue(
    new Response(JSON.stringify({ detail: "Revoked" }), { status: 403 }),
  );
  await expect(
    loadEmployeeInvocationSkills({ name: "reports", source: "team" }),
  ).rejects.toMatchObject({ status: 403 });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
