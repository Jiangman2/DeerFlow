import { describe, expect, test, rs } from "@rstest/core";

import { refreshDashboardQueries } from "@/core/dashboard/refresh";

describe("refreshDashboardQueries", () => {
  test("refetches every active dashboard and observability query", async () => {
    const refetchQueries = rs.fn().mockResolvedValue(undefined);

    await refreshDashboardQueries({ refetchQueries });

    expect(refetchQueries).toHaveBeenCalledTimes(4);
    expect(refetchQueries).toHaveBeenNthCalledWith(1, {
      queryKey: ["dashboard", "summary"],
      type: "active",
    });
    expect(refetchQueries).toHaveBeenNthCalledWith(2, {
      queryKey: ["dashboard", "run-rankings"],
      type: "active",
    });
    expect(refetchQueries).toHaveBeenNthCalledWith(3, {
      queryKey: ["dashboard"],
      type: "active",
      predicate: expect.any(Function),
    });
    expect(refetchQueries).toHaveBeenNthCalledWith(4, {
      queryKey: ["observability"],
      type: "active",
    });
  });
});
