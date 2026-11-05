import type { Announcement } from "./types";

export function getAnnouncementDisplayStatus(
  item: Pick<Announcement, "status" | "starts_at" | "ends_at">,
  now = Date.now(),
): string {
  if (item.status === "draft") return "草稿";
  if (item.status === "archived") return "已归档";
  if (item.starts_at && new Date(item.starts_at).getTime() > now)
    return "已排期";
  if (item.ends_at && new Date(item.ends_at).getTime() <= now) return "已结束";
  return "展示中";
}

export function validateAnnouncementWindow(
  startsAt: string | null | undefined,
  endsAt: string | null | undefined,
): string | null {
  if (!startsAt || !endsAt) return null;
  const starts = new Date(startsAt).getTime();
  const ends = new Date(endsAt).getTime();
  if (!Number.isFinite(starts) || !Number.isFinite(ends)) {
    return "时间格式无效";
  }
  return ends <= starts ? "结束时间必须晚于开始时间" : null;
}
