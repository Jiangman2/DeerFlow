import { describe, expect, test } from "@rstest/core";

import { getForcedTheme } from "@/components/theme-provider";

describe("getForcedTheme", () => {
  test("keeps the public landing page dark", () => {
    expect(getForcedTheme("/")).toBe("dark");
  });

  test("forces every workspace route to the light enterprise palette", () => {
    expect(getForcedTheme("/workspace/chats/new")).toBe("light");
    expect(getForcedTheme("/workspace/skills")).toBe("light");
    expect(getForcedTheme("/workspace/admin/dashboard")).toBe("light");
  });

  test("leaves unrelated routes under the user's selected theme", () => {
    expect(getForcedTheme("/login")).toBeUndefined();
    expect(getForcedTheme(null)).toBeUndefined();
  });
});
