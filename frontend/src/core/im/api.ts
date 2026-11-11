import { fetch as fetchWithAuth } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  ImCapabilities,
  ImSearchResponse,
  ImSessionTree,
  ImSessionTreeParams,
  ImThreadOrigin,
  ImThreadPage,
  ImThreadPageParams,
} from "./types";

async function readImAPIError(
  response: Response,
  fallback: string,
): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) {
      return body.detail;
    }
  } catch {
    // Fall through to the caller-provided message.
  }
  return fallback;
}

async function getImJSON<T>(path: string, fallback: string): Promise<T> {
  const response = await fetchWithAuth(`${getBackendBaseURL()}${path}`, {
    method: "GET",
  });
  if (!response.ok) {
    throw new Error(await readImAPIError(response, fallback));
  }
  return (await response.json()) as T;
}

export function fetchImCapabilities(): Promise<ImCapabilities> {
  return getImJSON<ImCapabilities>(
    "/api/im/capabilities",
    "无法读取飞书会话能力配置。",
  );
}

export function fetchImSessionTree(
  params: ImSessionTreeParams = {},
): Promise<ImSessionTree> {
  const search = new URLSearchParams();
  if (params.groupsLimit !== undefined) {
    search.set("groups_limit", String(params.groupsLimit));
  }
  if (params.threadsPerGroup !== undefined) {
    search.set("threads_per_group", String(params.threadsPerGroup));
  }
  if (params.groupOffset !== undefined) {
    search.set("group_offset", String(params.groupOffset));
  }
  const suffix = search.size > 0 ? `?${search.toString()}` : "";
  return getImJSON<ImSessionTree>(
    `/api/im/session-tree${suffix}`,
    "无法读取飞书会话列表。",
  );
}

export function fetchImGroupThreads(
  chatId: string,
  params: ImThreadPageParams = {},
): Promise<ImThreadPage> {
  const search = new URLSearchParams();
  search.set("limit", String(params.limit ?? 30));
  if (params.cursor) {
    search.set("cursor", params.cursor);
  }
  return getImJSON<ImThreadPage>(
    `/api/im/groups/${encodeURIComponent(chatId)}/threads?${search.toString()}`,
    "无法读取该群聊的会话记录。",
  );
}

export function fetchImPrivateThreads(
  params: ImThreadPageParams = {},
): Promise<ImThreadPage> {
  const search = new URLSearchParams();
  search.set("scope", "private");
  search.set("limit", String(params.limit ?? 30));
  if (params.cursor) {
    search.set("cursor", params.cursor);
  }
  return getImJSON<ImThreadPage>(
    `/api/im/threads?${search.toString()}`,
    "无法读取私聊会话记录。",
  );
}

export function searchImThreads(
  query: string,
  params: { limit?: number; offset?: number } = {},
): Promise<ImSearchResponse> {
  const search = new URLSearchParams();
  search.set("q", query);
  search.set("limit", String(params.limit ?? 20));
  search.set("offset", String(params.offset ?? 0));
  return getImJSON<ImSearchResponse>(
    `/api/im/threads/search?${search.toString()}`,
    "无法检索飞书会话。",
  );
}

export function fetchImThreadOrigin(threadId: string): Promise<ImThreadOrigin> {
  return getImJSON<ImThreadOrigin>(
    `/api/im/threads/${encodeURIComponent(threadId)}/origin`,
    "无法读取该会话的飞书来源。",
  );
}