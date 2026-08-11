import { describe, expect, test, rs } from "@rstest/core";

import { refreshDashboardQueries } from "@/core/dashboard/refresh";

describe("refreshDashboardQueries", () => {
  test("refetches every active dashboard and observability query", async () => {
    const refetchQueries = rs.fn().mockResolvedValue(undefined);

    await refreshDashboardQueries({ refetchQueries });

    expect(refetchQueries).toHaveBeenCalledTimes(2);
    expect(refetchQueries).toHaveBeenNthCalledWith(1, {
      queryKey: ["dashboard"],
      type: "active",
    });
    expect(refetchQueries).toHaveBeenNthCalledWith(2, {
      queryKey: ["observability"],
      type: "active",
    });
  });
});
