import { describe, expect, it } from "@rstest/core";

import {
  DASHBOARD_BAR_COLOR,
  dashboardBarStyle,
} from "@/core/dashboard/bar-style";

describe("dashboardBarStyle", () => {
  it("uses an explicit solid color for legacy enterprise browsers", () => {
    const style = dashboardBarStyle(5, 10);

    expect(style).toEqual({
      width: "50%",
      backgroundColor: DASHBOARD_BAR_COLOR,
    });
    expect(String(style.backgroundColor)).not.toContain("gradient");
  });

  it("keeps a visible minimum width for non-zero values", () => {
    expect(dashboardBarStyle(1, 1000).width).toBe("8%");
    expect(dashboardBarStyle(0, 1000).width).toBe("0%");
  });
});
