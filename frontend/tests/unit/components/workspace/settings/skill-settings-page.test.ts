import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "@rstest/core";

const source = readFileSync(
  resolve(
    process.cwd(),
    "src/components/workspace/settings/skill-settings-page.tsx",
  ),
  "utf8",
);

describe("public Skill settings", () => {
  test("renders public Skill state as read-only", () => {
    expect(source).toContain(
      'const isPublicReadOnly = skill.category === "public" && !isAdmin',
    );
    expect(source).toContain("aria-readonly={isPublicReadOnly}");
    expect(source).toContain("isPublicReadOnly ||");
  });

  test("does not attach a public Skill toggle mutation", () => {
    expect(source).toMatch(
      /onCheckedChange=\{\s*isPublicReadOnly\s*\? undefined/,
    );
  });
});
