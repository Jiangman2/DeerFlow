"use client";

import { useState, type ComponentProps } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  activityCalendar,
  activityHeatmapColumns,
  activityTileCount,
  activityWeeks,
  useAccountActivity,
} from "@/core/console/account-activity";
import { useI18n } from "@/core/i18n/hooks";
import { usageAnalyticsCopy } from "@/core/i18n/usage-analytics";

function UsageMark({
  label,
  ...props
}: ComponentProps<"button"> & { label: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button type="button" aria-label={label} {...props} />
      </TooltipTrigger>
      <TooltipContent className="max-w-72 whitespace-pre-line" side="top">
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

export function AccountActivityPanel() {
  const query = useAccountActivity();
  const { locale } = useI18n();
  const copy = usageAnalyticsCopy(locale);
  const [mode, setMode] = useState("daily");
  const [detail, setDetail] = useState("");
  const data = query.isError ? undefined : query.data;
  const compact = (n: number) =>
    new Intl.NumberFormat(locale, {
      notation: n >= 10_000 ? "compact" : "standard",
      maximumFractionDigits: 2,
    }).format(n);
  const duration = data
    ? `${Math.floor(data.longest_task_seconds / 3600)} ${copy.hours} ${Math.floor(data.longest_task_seconds / 60) % 60} ${copy.minutes}`
    : "—";
  const stats = [
    [copy.peak, data ? compact(data.peak_daily_tokens) : "—"],
    [copy.longest, duration],
    [
      copy.longestStreak,
      data ? `${data.longest_streak_days} ${copy.days}` : "—",
    ],
    [
      copy.currentStreak,
      data ? `${data.current_streak_days} ${copy.days}` : "—",
    ],
  ];
  const days = data?.days ?? [];
  const maximum = Math.max(1, ...days.map((d) => d.tokens));
  const calendar = activityCalendar(days, locale);
  const weeks = activityWeeks(days);
  const columns = activityHeatmapColumns(
    days,
    data?.total_tokens ?? 0,
    data?.recorded_since ?? null,
  );
  const chartMaximum = Math.max(
    1,
    ...columns.map(
      (week) => (mode === "weekly" ? week.tokens : week.cumulative) ?? 0,
    ),
  );
  const dateLabel = (value: string) =>
    new Date(`${value}T00:00:00Z`).toLocaleDateString(locale, {
      year: "numeric",
      month: "long",
      day: "numeric",
      timeZone: "UTC",
    });
  const weekLabel = (week: (typeof columns)[number]) => {
    const period = locale.startsWith("zh")
      ? `${dateLabel(week.date)} ${copy.weekStarting}`
      : `${copy.weekStarting} ${dateLabel(week.date)}`;
    const heading =
      mode === "cumulative" ? `${copy.cumulativeThrough} ${period}` : period;
    const value = mode === "weekly" ? week.tokens : week.cumulative;
    return value === null
      ? `${heading}\n${copy.unknown}`
      : `${heading}\n${compact(value)} ${copy.exact} (${value.toLocaleString(locale)} ${copy.exact})\n${copy.asOf} ${dateLabel(week.observedThrough)}`;
  };
  const select = (day: { date: string; tokens: number; runs: number }) =>
    `${day.date} · ${day.tokens.toLocaleString(locale)} ${copy.exact} · ${day.runs} ${copy.runs}`;

  return (
    <Card
      className="mt-4 min-w-0 [contain:inline-size]"
      data-testid="account-activity"
    >
      <CardHeader>
        <CardTitle>{copy.title}</CardTitle>
        <CardDescription>{copy.scope}</CardDescription>
      </CardHeader>
      <CardContent className="flex min-w-0 flex-col gap-5">
        <dl className="grid grid-cols-2 gap-4 xl:grid-cols-4">
          {stats.map(([label, value]) => (
            <div key={label}>
              <dd className="text-lg font-semibold tabular-nums">{value}</dd>
              <dt className="text-muted-foreground text-xs">{label}</dt>
            </div>
          ))}
        </dl>
        {query.isPending ? (
          <Skeleton className="h-36 w-full" aria-label={copy.loading} />
        ) : query.isError ? (
          <div
            role="status"
            className="flex flex-wrap items-center gap-3 text-sm"
          >
            <span>{copy.unavailable}</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void query.refetch()}
            >
              {copy.retry}
            </Button>
          </div>
        ) : data ? (
          <>
            <ToggleGroup
              type="single"
              size="sm"
              spacing={2}
              value={mode}
              onValueChange={(value) => {
                if (value) {
                  setMode(value);
                  setDetail("");
                }
              }}
              aria-label={copy.title}
            >
              <ToggleGroupItem value="daily">{copy.daily}</ToggleGroupItem>
              <ToggleGroupItem value="weekly">{copy.weekly}</ToggleGroupItem>
              <ToggleGroupItem value="cumulative">
                {copy.cumulative}
              </ToggleGroupItem>
            </ToggleGroup>
            <div
              className="min-w-0 overflow-x-auto pb-2"
              data-testid="activity-chart-scroll"
            >
              <div className="min-w-[640px]">
                {mode === "daily" ? (
                  <div
                    data-testid="activity-heatmap"
                    data-mode="daily"
                    className="grid grid-flow-col grid-rows-7 gap-1"
                    style={{
                      gridTemplateColumns: `repeat(${calendar.columns}, minmax(0, 1fr))`,
                    }}
                    aria-label={copy.selectDay}
                  >
                    {calendar.cells.map((day, index) => {
                      if (!day || day.future)
                        return (
                          <span
                            key={`pad-${index}`}
                            className="aspect-square rounded-[4px] bg-[#f3f4f6]"
                            aria-hidden="true"
                          />
                        );
                      const known =
                        !!data.recorded_since &&
                        day.date >= data.recorded_since;
                      const week = weeks[Math.floor(index / 7)];
                      const label = known
                        ? `${select(day)}\n${copy.weeklyTotal} · ${(week?.tokens ?? 0).toLocaleString(locale)} ${copy.exact}`
                        : `${day.date} · ${copy.unknown}`;
                      return (
                        <UsageMark
                          data-testid="activity-day"
                          key={day.date}
                          label={label}
                          onClick={() => setDetail(label)}
                          className="focus-visible:outline-primary relative aspect-square min-w-0 overflow-hidden rounded-[4px] bg-[#f3f4f6] focus-visible:outline-2 focus-visible:outline-offset-2"
                        >
                          {known && day.tokens > 0 ? (
                            <span
                              className="absolute inset-0 bg-[#527fff]"
                              style={{
                                opacity:
                                  0.2 + 0.8 * Math.sqrt(day.tokens / maximum),
                              }}
                            />
                          ) : null}
                        </UsageMark>
                      );
                    })}
                  </div>
                ) : (
                  <div
                    data-testid="activity-heatmap"
                    data-mode={mode}
                    className="grid items-stretch gap-1"
                    style={{
                      gridTemplateColumns: `repeat(${calendar.columns}, minmax(0, 1fr))`,
                    }}
                    aria-label={
                      mode === "weekly" ? copy.weekly : copy.cumulative
                    }
                  >
                    {columns.map((week) => {
                      const value =
                        mode === "weekly" ? week.tokens : week.cumulative;
                      const filled = activityTileCount(value, chartMaximum);
                      const tiles = Array.from({ length: 7 }, (_, row) => (
                        <span
                          key={row}
                          aria-hidden="true"
                          data-filled={row >= 7 - filled}
                          className={
                            row >= 7 - filled
                              ? "aspect-square rounded-[4px] bg-[#527fff] group-hover:bg-[#304f97] group-focus-visible:bg-[#304f97] group-data-[selected=true]:bg-[#304f97]"
                              : "aspect-square rounded-[4px] bg-[#f3f4f6] group-hover:bg-[#dce5f7] group-focus-visible:bg-[#dce5f7] group-data-[selected=true]:bg-[#dce5f7]"
                          }
                        />
                      ));
                      if (week.future)
                        return (
                          <div
                            key={week.date}
                            className="grid grid-rows-7 gap-1"
                            aria-hidden="true"
                          >
                            {tiles}
                          </div>
                        );
                      const label = weekLabel(week);
                      return (
                        <UsageMark
                          data-testid="activity-week"
                          data-week-start={week.date}
                          data-selected={detail === label}
                          key={week.date}
                          label={label}
                          onClick={() => setDetail(label)}
                          className="group focus-visible:outline-primary grid min-w-0 grid-rows-7 gap-1 rounded-[4px] bg-transparent p-0 focus-visible:outline-2 focus-visible:outline-offset-2"
                        >
                          {tiles}
                        </UsageMark>
                      );
                    })}
                  </div>
                )}
                <div
                  data-testid="activity-months"
                  className="text-muted-foreground mt-3 grid text-xs"
                  style={{
                    gridTemplateColumns: `repeat(${calendar.columns}, minmax(0, 1fr))`,
                  }}
                >
                  {calendar.months.map((month) => (
                    <span
                      key={month.label}
                      className="whitespace-nowrap"
                      style={{ gridColumnStart: month.column }}
                    >
                      {month.label}
                    </span>
                  ))}
                </div>
              </div>
            </div>
            <div className="text-muted-foreground flex flex-wrap justify-between gap-2 text-xs">
              {mode === "daily" ? (
                <span
                  className="flex items-center gap-1"
                  aria-label={`${copy.less} / ${copy.more}`}
                >
                  {copy.less}
                  {[0, 0.2, 0.45, 0.7, 1].map((opacity) => (
                    <span
                      key={opacity}
                      className="bg-muted relative size-3 rounded-sm"
                    >
                      <span
                        className="bg-primary absolute inset-0 rounded-sm"
                        style={{ opacity }}
                      />
                    </span>
                  ))}
                  {copy.more}
                </span>
              ) : null}
            </div>
            <p role="status" className="min-h-5 text-xs">
              {detail ||
                (data.recorded_since ? copy.selectDay : copy.noRecords)}
            </p>
          </>
        ) : null}
      </CardContent>
      <CardFooter className="text-muted-foreground text-xs leading-relaxed">
        {copy.streakNote}
      </CardFooter>
    </Card>
  );
}
