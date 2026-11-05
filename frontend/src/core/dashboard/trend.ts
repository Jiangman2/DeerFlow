import type { UserActivityPoint } from "./types";

export function activityBarPercent(value: number, max: number): number {
  if (value <= 0) return 0;
  return Math.max(5, Math.round((value / Math.max(1, max)) * 100));
}

export function activityPeriodLabel(
  item: Pick<UserActivityPoint, "date" | "end_date">,
  bucket: string,
): string {
  const compact = (value: string) => value.slice(5).replace("-", "/");
  if (bucket !== "day" && item.end_date !== item.date) {
    return `${compact(item.date)}–${compact(item.end_date)}`;
  }
  return compact(item.date);
}

export function activityChartMinWidth(itemCount: number): number {
  return Math.max(560, itemCount * 48);
}

export function activitySharedMax(
  items: Array<Pick<UserActivityPoint, "threads" | "active_users">>,
): number {
  return Math.max(
    1,
    ...items.flatMap((item) => [item.threads, item.active_users]),
  );
}
