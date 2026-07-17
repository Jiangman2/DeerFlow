import { afterEach, expect, test, rs } from "@rstest/core";

import { brand } from "@/brand";

const original = process.env.NEXT_PUBLIC_APP_VERSION;

afterEach(() => {
  rs.resetModules();
  if (original === undefined) {
    delete process.env.NEXT_PUBLIC_APP_VERSION;
  } else {
    process.env.NEXT_PUBLIC_APP_VERSION = original;
  }
});

test("aboutMarkdown heading interpolates the app version", async () => {
  process.env.NEXT_PUBLIC_APP_VERSION = "9.9.9-test";
  const { aboutMarkdown } =
    await import("@/components/workspace/settings/about-content");
  expect(aboutMarkdown).toContain(`# 关于${brand.productName} 9.9.9-test`);
  expect(aboutMarkdown).toContain(`> **${brand.productName}**`);
});

test("aboutMarkdown heading reflects the package version when env is unset", async () => {
  delete process.env.NEXT_PUBLIC_APP_VERSION;
  const { APP_VERSION } = await import("@/version");
  const { aboutMarkdown } =
    await import("@/components/workspace/settings/about-content");
  expect(aboutMarkdown).toContain(`# 关于${brand.productName} ${APP_VERSION}`);
});
