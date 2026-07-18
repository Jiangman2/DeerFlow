import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  Announcement,
  AnnouncementInput,
  AnnouncementStatus,
} from "./types";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try {
      const payload = JSON.parse(text) as { detail?: string };
      message = payload.detail ?? text;
    } catch {
      // Keep the plain response body when it is not JSON.
    }
    if (response.status === 409) {
      message = "该展示时段与另一条已发布公告重叠，请先调整时间或撤下原公告。";
    }
    throw new Error(message || `请求失败（${response.status}）`);
  }
  return (await response.json()) as T;
}

export async function getActiveAnnouncement(): Promise<Announcement | null> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/announcements/active`,
  );
  const data = await readJson<{ announcement: Announcement | null }>(response);
  return data.announcement;
}

export async function listAnnouncements(): Promise<Announcement[]> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/announcements`,
  );
  const data = await readJson<{ items: Announcement[] }>(response);
  return data.items;
}

export async function createAnnouncement(
  input: AnnouncementInput,
): Promise<Announcement> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/announcements`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  const data = await readJson<{ item: Announcement }>(response);
  return data.item;
}

export async function updateAnnouncement(
  id: string,
  input: Partial<AnnouncementInput>,
): Promise<Announcement> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/announcements/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  const data = await readJson<{ item: Announcement }>(response);
  return data.item;
}

export async function setAnnouncementStatus(
  id: string,
  status: AnnouncementStatus,
): Promise<Announcement> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/announcements/${encodeURIComponent(
      id,
    )}/status`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    },
  );
  const data = await readJson<{ item: Announcement }>(response);
  return data.item;
}
