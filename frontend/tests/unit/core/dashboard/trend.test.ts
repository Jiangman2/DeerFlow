import { expect, test } from "@rstest/core";

import {
  activityBarPercent,
  activityChartMinWidth,
  activityPeriodLabel,
  activitySharedMax,
} from "@/core/dashboard/trend";

test("activity trend uses readable percentages and weekly labels", () => {
  expect(activityBarPercent(0, 10)).toBe(0);
  expect(activityBarPercent(1, 100)).toBe(5);
  expect(activityBarPercent(50, 100)).toBe(50);
  expect(
    activityPeriodLabel({ date: "2026-04-01", end_date: "2026-04-07" }, "week"),
  ).toBe("04/01–04/07");
  expect(
    activityPeriodLabel(
      { date: "2026-01-01", end_date: "2026-01-30" },
      "month",
    ),
  ).toBe("01/01–01/30");
  expect(activityChartMinWidth(13)).toBe(624);
  expect(
    activitySharedMax([
      { threads: 10, active_users: 2 },
      { threads: 4, active_users: 3 },
    ]),
  ).toBe(10);
});
