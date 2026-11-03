import { describe, expect, test } from "@rstest/core";

import { dashboardRefreshInterval } from "@/core/dashboard/hooks";

describe("dashboardRefreshInterval", () => {
  test("refreshes only short rolling windows once per minute", () => {
    expect(dashboardRefreshInterval({ hours: 24 })).toBe(60_000);
    expect(dashboardRefreshInterval({ hours: 168 })).toBe(60_000);
  });

  test("does not poll expensive long or fixed custom ranges", () => {
    expect(dashboardRefreshInterval({ hours: 24 * 30 })).toBe(false);
    expect(
      dashboardRefreshInterval({
        hours: 24,
        start_at: "2026-09-01T00:00:00Z",
        end_at: "2026-09-02T00:00:00Z",
      }),
    ).toBe(false);
  });
});
