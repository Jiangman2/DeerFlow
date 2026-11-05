import { expect, test } from "@rstest/core";

import { zhCN } from "@/core/i18n";

test("Chinese agent creation saves Chinese descriptions and SOUL content by default", () => {
  const message = zhCN.agents.saveCommandMessage;

  expect(message).toContain("简体中文");
  expect(message).toContain("一句话描述");
  expect(message).not.toContain("英文初始 SOUL.md");
});
