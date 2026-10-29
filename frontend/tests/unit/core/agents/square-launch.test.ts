import { beforeEach, describe, expect, it, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "/backend" }));

import { recordSquareEmployeeLaunch } from "@/core/agents/api";
import type { Agent } from "@/core/agents/types";
import { fetch } from "@/core/api/fetcher";

const mockedFetch = rs.mocked(fetch);
const agent: Agent = {
  name: "政策/助手",
  description: "",
  model: null,
  skills: null,
  tool_groups: null,
  source: "team",
  hot_identity: "a".repeat(64),
};
beforeEach(() => {
  mockedFetch.mockReset();
});

describe("square launch attribution", () => {
  it("uses the verified publication token, encoded name and existing CSRF fetch wrapper", async () => {
    mockedFetch.mockResolvedValue(new Response(null, { status: 204 }));
    await recordSquareEmployeeLaunch(agent);
    expect(mockedFetch).toHaveBeenCalledWith(
      "/backend/api/employee-directory/team/%E6%94%BF%E7%AD%96%2F%E5%8A%A9%E6%89%8B/click",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        keepalive: true,
        body: JSON.stringify({ expected_identity: agent.hot_identity }),
      },
    );
  });
  it("does not record personal employees or guess a missing publication identity", async () => {
    await recordSquareEmployeeLaunch({ ...agent, source: "personal" });
    await recordSquareEmployeeLaunch({ ...agent, hot_identity: undefined });
    expect(mockedFetch).not.toHaveBeenCalled();
  });
  it("classifies external cards by their actual launch target and does not retry failed writes", async () => {
    mockedFetch.mockResolvedValue(new Response(null, { status: 503 }));
    await expect(
      recordSquareEmployeeLaunch({
        ...agent,
        external_url: "https://example.com",
      }),
    ).rejects.toThrow();
    expect(mockedFetch).toHaveBeenCalledTimes(1);
    expect(mockedFetch.mock.calls[0]?.[0]).toEqual(
      expect.stringContaining("/external/"),
    );
  });
});
