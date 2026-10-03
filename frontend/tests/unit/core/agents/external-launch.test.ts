import { expect, test } from "@rstest/core";

import { resolveExternalAgentLaunchHref } from "@/core/agents/external-launch";
import { SHARED_EXTERNAL_AGENTS } from "@/core/agents/shared-agents";

const CNOOCCLAW_AGENT_URLS = [
  "https://cnoocclaw.cnooc.com.cn/zywdjxzj/",
  "https://cnoocclaw.cnooc.com.cn/dzsjtpzj/",
  "https://cnoocclaw.cnooc.com.cn/zjbgjszj/",
  "https://cnoocclaw.cnooc.com.cn/zdsczj/",
  "https://cnoocclaw.cnooc.com.cn/qqyjbgbxzj/",
  "https://cnoocclaw.cnooc.com.cn/yjzs/",
] as const;

test("keeps all six current CNOOCClaw expert aliases as direct links", () => {
  for (const url of CNOOCCLAW_AGENT_URLS) {
    expect(resolveExternalAgentLaunchHref(url)).toBe(url);
  }
});

test("publishes the email assistant as the sixth fixed external employee", () => {
  const assistant = SHARED_EXTERNAL_AGENTS.find(
    (agent) => agent.name === "邮件智能整理助手",
  );

  expect(assistant).toMatchObject({
    description:
      "邮件智能整理助手，支持会议与待办一键写入海能work日历和待办，并进行智能提醒。",
    category_id: "comprehensive-management",
    external_url: "https://cnoocclaw.cnooc.com.cn/yjzs/",
  });
});

test("routes legacy Data Lake expert links through the authenticated launch API", () => {
  expect(
    resolveExternalAgentLaunchHref("https://datalake.cnooc/zywdjxzj"),
  ).toBe("/api/cnooc/external-agents/zywdjxzj/launch");
  expect(resolveExternalAgentLaunchHref("https://datalake.cnooc/yjzs")).toBe(
    "https://datalake.cnooc/yjzs",
  );
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
