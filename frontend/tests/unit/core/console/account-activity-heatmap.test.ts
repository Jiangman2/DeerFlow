import { describe, expect, it } from "@rstest/core";

import {
  activityCalendar,
  activityHeatmapColumns,
  activityTileCount,
} from "@/core/console/account-activity";

describe("weekly and lifetime cumulative heatmap", () => {
  const days = [
    { date: "2026-10-30", tokens: 10, runs: 1 },
    { date: "2026-10-31", tokens: 20, runs: 1 },
    { date: "2026-11-01", tokens: 30, runs: 1 },
    { date: "2026-11-02", tokens: 40, runs: 2 },
  ];

  it("aligns weeks to the same twelve-month calendar, including the month boundary", () => {
    const columns = activityHeatmapColumns(days, 500, "2026-10-30");
    expect(columns).toHaveLength(activityCalendar(days, "zh-CN").columns);
    const first = columns.find((week) => week.date === "2026-10-26")!;
    expect(first.tokens).toBe(60);
    expect(first.cumulative).toBe(460);
    expect(first.observedThrough).toBe("2026-11-01");
    const current = columns.find((week) => week.date === "2026-11-02")!;
    expect(current.tokens).toBe(40);
    expect(current.cumulative).toBe(500);
    expect(current.observedThrough).toBe("2026-11-02");
  });

  it("keeps unrecorded and future columns unknown, without inventing zero usage", () => {
    const columns = activityHeatmapColumns(days, 100, "2026-10-30");
    expect(
      columns
        .filter((week) => !week.known)
        .every((week) => week.tokens === null && week.cumulative === null),
    ).toBe(true);
    expect(columns.at(-1)!.future).toBe(true);
    expect(
      activityHeatmapColumns(days, 0, null).every((week) => !week.known),
    ).toBe(true);
    expect(activityHeatmapColumns([], 0, null)).toEqual([]);
  });

  it("retains zero-usage known weeks and lifetime totals preceding the displayed year", () => {
    const quiet = days.map((day) => ({ ...day, tokens: 0, runs: 0 }));
    const columns = activityHeatmapColumns(quiet, 1200, "2025-01-01");
    expect(columns.find((week) => week.date === "2026-11-02")).toMatchObject({
      tokens: 0,
      cumulative: 1200,
      known: true,
    });
  });

  it("fills seven square tiles upward without drawing a nonzero tile for zero or missing data", () => {
    expect(activityTileCount(null, 700)).toBe(0);
    expect(activityTileCount(0, 700)).toBe(0);
    expect(activityTileCount(1, 700)).toBe(1);
    expect(activityTileCount(301, 700)).toBe(4);
    expect(activityTileCount(700, 700)).toBe(7);
    expect(activityTileCount(900, 700)).toBe(7);
  });
});
