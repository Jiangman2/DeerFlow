"use client";

import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { fetch } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { getBackendBaseURL } from "@/core/config";

const count = z.number().int().nonnegative();
export const activitySchema = z.object({
  scope: z.literal("current_user_recorded_platform_runs"),
  timezone: z.literal("Asia/Shanghai"),
  attribution: z.literal("run_start_day"),
  total_tokens: count,
  input_tokens: count,
  output_tokens: count,
  peak_daily_tokens: count,
  longest_task_seconds: z.number().nonnegative(),
  longest_streak_days: count,
  current_streak_days: count,
  recorded_since: z.string().nullable(),
  generated_at: z.string(),
  days: z
    .array(z.object({ date: z.string(), tokens: count, runs: count }))
    .max(366),
});
export type AccountActivity = z.infer<typeof activitySchema>;

export function useAccountActivity() {
  const { user } = useAuth();
  return useQuery({
    queryKey: ["account-activity", user?.id],
    enabled: !!user?.id,
    queryFn: async ({ signal }) => {
      const response = await fetch(
        `${getBackendBaseURL()}/api/console/activity`,
        { signal },
      );
      if (!response.ok) throw new Error("Usage history unavailable");
      return activitySchema.parse(await response.json());
    },
    staleTime: 300_000,
    refetchInterval: 300_000,
    refetchIntervalInBackground: false,
    retry: false,
  });
}

export function activityWeeks(days: AccountActivity["days"]) {
  const weeks: { date: string; tokens: number; runs: number }[] = [];
  for (const day of days) {
    const start = new Date(`${day.date}T00:00:00Z`);
    start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));
    const key = start.toISOString().slice(0, 10);
    const previous = weeks.at(-1);
    if (previous?.date === key) {
      previous.tokens += day.tokens;
      previous.runs += day.runs;
    } else weeks.push({ ...day, date: key });
  }
  return weeks;
}

/** Stable twelve-month calendar in the server's Shanghai date, including blank future cells. */
export function activityCalendar(
  days: AccountActivity["days"],
  locale: string,
) {
  const last = days.at(-1)?.date;
  if (!last) return { cells: [], months: [], columns: 0 };
  const today = new Date(`${last}T00:00:00Z`);
  const start = new Date(
    Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - 11, 1),
  );
  const end = new Date(
    Date.UTC(today.getUTCFullYear(), today.getUTCMonth() + 1, 0),
  );
  const offset = (start.getUTCDay() + 6) % 7;
  const length = Math.round((end.getTime() - start.getTime()) / 86400000) + 1;
  const columns = Math.ceil((offset + length) / 7);
  const lookup = new Map(days.map((day) => [day.date, day]));
  const cells = Array.from({ length: columns * 7 }, (_, index) => {
    const date = new Date(start.getTime() + (index - offset) * 86400000)
      .toISOString()
      .slice(0, 10);
    return index < offset || index >= offset + length
      ? null
      : {
          date,
          tokens: lookup.get(date)?.tokens ?? 0,
          runs: lookup.get(date)?.runs ?? 0,
          future: date > last,
        };
  });
  const months = Array.from({ length: 12 }, (_, index) => {
    const at = new Date(
      Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + index, 1),
    );
    return {
      label: at.toLocaleDateString(locale, { month: "short", timeZone: "UTC" }),
      column:
        Math.floor(
          (offset + Math.round((at.getTime() - start.getTime()) / 86400000)) /
            7,
        ) + 1,
    };
  });
  return { cells, months, columns };
}

/** One seven-tile column per calendar week, including unknown/future columns. */
export function activityHeatmapColumns(
  days: AccountActivity["days"],
  totalTokens: number,
  recordedSince: string | null,
) {
  const calendar = activityCalendar(days, "en-US");
  const weeks = new Map(activityWeeks(days).map((week) => [week.date, week]));
  // Lifetime cumulative usage must retain usage preceding the visible year.
  let cumulative = Math.max(
    0,
    totalTokens - days.reduce((sum, day) => sum + day.tokens, 0),
  );
  const today = days.at(-1)?.date ?? "";
  return Array.from({ length: calendar.columns }, (_, index) => {
    const cells = calendar.cells.slice(index * 7, index * 7 + 7);
    const first = cells.find((day) => day !== null)!;
    const monday = new Date(`${first.date}T00:00:00Z`);
    monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
    const date = monday.toISOString().slice(0, 10);
    const last = cells.filter((day) => day !== null).at(-1)!.date;
    const observedThrough = last > today ? today : last;
    const known =
      !!recordedSince && date <= today && observedThrough >= recordedSince;
    const tokens = weeks.get(date)?.tokens ?? 0;
    cumulative += tokens;
    return {
      date,
      observedThrough,
      known,
      future: date > today,
      tokens: known ? tokens : null,
      cumulative: known ? cumulative : null,
    };
  });
}

export function activityTileCount(value: number | null, maximum: number) {
  return value === null || value <= 0
    ? 0
    : Math.min(7, Math.max(1, Math.ceil((value / Math.max(1, maximum)) * 7)));
}
