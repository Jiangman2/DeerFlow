import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "@rstest/core";

const source = readFileSync(
  resolve(
    process.cwd(),
    "src/components/workspace/settings/settings-dialog.tsx",
  ),
  "utf8",
);

describe("settings dialog viewport layout", () => {
  test("hides the appearance entry while preserving the underlying theme module", () => {
    expect(source).not.toContain("AppearanceSettingsPage");
    expect(source).not.toContain('id: "appearance"');
    expect(source).toContain('defaultSection = "account"');
  });

  test("uses the available desktop viewport instead of a fixed 75 percent height", () => {
    expect(source).toContain("sm:h-[calc(100vh-2rem)]");
    expect(source).toContain("sm:max-h-[56rem]");
    expect(source).not.toContain("sm:h-[75vh]");
  });

  test("keeps nested settings panes inside the dialog scroll contract", () => {
    expect(source).toMatch(/flex-1[^\"]*overflow-hidden/);
    expect(source).toContain("h-full min-h-0 min-w-0");
  });

  test("keeps content spacing independent from the safe-area shell", () => {
    expect(source).toContain(
      'className="mobile-fullscreen-surface app-safe-area flex w-screen max-w-none min-w-0 flex-col gap-0',
    );
    expect(source).toContain('data-testid="settings-dialog-frame"');
    expect(source).toContain(
      'className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-hidden p-4 sm:p-6"',
    );
  });
});
