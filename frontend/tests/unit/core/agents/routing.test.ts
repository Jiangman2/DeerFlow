import { describe, expect, it } from "@rstest/core";

import {
  agentChatPath,
  decodeAgentNameRouteParam,
  parseAgentSource,
} from "@/core/agents/routing";
import { pathOfThread } from "@/core/threads/utils";

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

it("preserves the selected namespace through navigation and thread history", () => {
  expect(agentChatPath("reports", "new", "team")).toBe(
    "/workspace/agents/reports/chats/new?source=team",
  );
  expect(
    pathOfThread({
      thread_id: "t1",
      metadata: { agent_name: "reports", agent_source: "team" },
    }),
  ).toBe("/workspace/agents/reports/chats/t1?source=team");
  expect(
    pathOfThread("t1", { agent_name: "reports", agent_source: "personal" }),
  ).toBe("/workspace/agents/reports/chats/t1?source=personal");
  expect(agentChatPath("reports", "new")).toBe(
    "/workspace/agents/reports/chats/new",
  );
});

it("rejects invalid sources instead of silently selecting a different employee", () => {
  expect(() => parseAgentSource("../personal")).toThrow();
  expect(parseAgentSource(null)).toBeUndefined();
});

it("keeps malformed historical origins visible without crashing the thread list", () => {
  expect(pathOfThread({ thread_id: "t1", metadata: { agent_name: "reports", agent_source: {} } })).toBe(
    "/workspace/agents/reports/chats/t1?source=invalid",
  );
  expect(pathOfThread({
    thread_id: "t1",
    context: { agent_name: "personal-old" },
    metadata: { agent_name: "reports", agent_source: "team" },
  })).toBe("/workspace/agents/personal-old/chats/t1");
});
