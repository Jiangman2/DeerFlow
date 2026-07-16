import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { SiteNavigationInput, SiteNavigationItem } from "./types";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function listSiteNavigation(): Promise<SiteNavigationItem[]> {
  const response = await fetch(`${getBackendBaseURL()}/api/site-navigation`);
  const data = await readJson<{ items: SiteNavigationItem[] }>(response);
  return data.items;
}

export async function listAdminSiteNavigation(): Promise<
  SiteNavigationItem[]
> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/site-navigation`,
  );
  const data = await readJson<{ items: SiteNavigationItem[] }>(response);
  return data.items;
}

export async function createSiteNavigation(
  input: SiteNavigationInput,
): Promise<SiteNavigationItem> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/site-navigation`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  const data = await readJson<{ item: SiteNavigationItem }>(response);
  return data.item;
}

export async function updateSiteNavigation(
  id: string,
  input: Partial<SiteNavigationInput>,
): Promise<SiteNavigationItem> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/site-navigation/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  const data = await readJson<{ item: SiteNavigationItem }>(response);
  return data.item;
}

export async function deleteSiteNavigation(id: string): Promise<void> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/site-navigation/${encodeURIComponent(id)}`,
    {
      method: "DELETE",
    },
  );
  await readJson<{ success: boolean }>(response);
}
