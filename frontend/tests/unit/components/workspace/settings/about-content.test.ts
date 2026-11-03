import { expect, test } from "@rstest/core";

import { brand } from "@/brand";
import { aboutMarkdown } from "@/components/workspace/settings/about-content";

test("aboutMarkdown heading contains only the product name", () => {
  expect(aboutMarkdown).toContain(`# 关于${brand.productName}\n`);
  expect(aboutMarkdown).toContain(`> **${brand.productName}**`);
  expect(aboutMarkdown).not.toMatch(/^# .*?\d+\.\d+\.\d+/m);
});
