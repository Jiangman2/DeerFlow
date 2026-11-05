import { describe, expect, it } from "@rstest/core";

import { decodeAgentNameRouteParam } from "@/core/agents/routing";

describe("decodeAgentNameRouteParam", () => {
  it("decodes a Chinese custom-agent route segment exactly once", () => {
    expect(
      decodeAgentNameRouteParam("%E6%9C%88%E6%8A%A5%E5%8A%A9%E6%89%8B"),
    ).toBe("月报助手");
  });

  it("keeps an already decoded Chinese name unchanged", () => {
    expect(decodeAgentNameRouteParam("月报助手")).toBe("月报助手");
  });

  it("preserves a malformed legacy percent sequence", () => {
    expect(decodeAgentNameRouteParam("月报%助手")).toBe("月报%助手");
  });
});
