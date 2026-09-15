import { expect, test } from "@rstest/core";

import { resolveExternalAgentLaunchHref } from "@/core/agents/external-launch";

const CNOOCCLAW_AGENT_URLS = [
  "https://cnoocclaw.cnooc.com.cn/zywdjxzj/",
  "https://cnoocclaw.cnooc.com.cn/dzsjtpzj/",
  "https://cnoocclaw.cnooc.com.cn/zjbgjszj/",
  "https://cnoocclaw.cnooc.com.cn/zdsczj/",
  "https://cnoocclaw.cnooc.com.cn/qqyjbgbxzj/",
] as const;

test("keeps all five current CNOOCClaw expert aliases as direct links", () => {
  for (const url of CNOOCCLAW_AGENT_URLS) {
    expect(resolveExternalAgentLaunchHref(url)).toBe(url);
  }
});

test("routes legacy Data Lake expert links through the authenticated launch API", () => {
  expect(
    resolveExternalAgentLaunchHref("https://datalake.cnooc/zywdjxzj"),
  ).toBe("/api/cnooc/external-agents/zywdjxzj/launch");
});

test("does not rewrite an unapproved host or path", () => {
  expect(resolveExternalAgentLaunchHref("https://example.com/zywdjxzj")).toBe(
    "https://example.com/zywdjxzj",
  );
  expect(
    resolveExternalAgentLaunchHref(
      "https://cnoocclaw.cnooc.com.cn/unapproved-agent/",
    ),
  ).toBe("https://cnoocclaw.cnooc.com.cn/unapproved-agent/");
});
