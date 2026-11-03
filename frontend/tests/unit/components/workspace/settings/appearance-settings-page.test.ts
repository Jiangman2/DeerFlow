import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "@rstest/core";

const source = readFileSync(
  resolve(
    process.cwd(),
    "src/components/workspace/settings/appearance-settings-page.tsx",
  ),
  "utf8",
);

describe("appearance settings enterprise-browser compatibility", () => {
  test("theme previews avoid slash-alpha color utilities", () => {
    expect(source).not.toMatch(
      /(?:bg|border|ring|text|shadow)-(?:current|primary|border)\/\d+/,
    );
  });

  test("theme previews use explicit light and dark skeleton colors", () => {
    expect(source).toContain("bg-slate-200");
    expect(source).toContain("bg-slate-50");
    expect(source).toContain("bg-neutral-600");
    expect(source).toContain("bg-neutral-800");
  });
});
