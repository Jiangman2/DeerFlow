import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  CreateWeKnoraKnowledgeBaseInput,
  CreateWeKnoraManualKnowledgeInput,
  UploadWeKnoraKnowledgeFileInput,
  WeKnoraKnowledge,
  WeKnoraKnowledgeBase,
  WeKnoraListResponse,
  WeKnoraTag,
} from "./types";

async function readJson<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String(data.detail)
        : response.statusText;
    throw new Error(detail || "WeKnora request failed");
  }
  return data as T;
}

function apiPath(path: string): string {
  return `${getBackendBaseURL()}${path}`;
}

export async function listWeKnoraKnowledgeBases(): Promise<
  WeKnoraListResponse<WeKnoraKnowledgeBase>
> {
  const response = await fetch(apiPath("/api/weknora/knowledge-bases"));
  return readJson<WeKnoraListResponse<WeKnoraKnowledgeBase>>(response);
}

export async function createWeKnoraKnowledgeBase(
  input: CreateWeKnoraKnowledgeBaseInput,
): Promise<{ data?: WeKnoraKnowledgeBase }> {
  const response = await fetch(apiPath("/api/weknora/knowledge-bases"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      description: input.description ?? "",
    }),
  });
  return readJson<{ data?: WeKnoraKnowledgeBase }>(response);
}

export async function listWeKnoraKnowledge(
  knowledgeBaseId: string,
  tenantId?: string | number,
  page = 1,
  pageSize = 100,
): Promise<WeKnoraListResponse<WeKnoraKnowledge>> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (tenantId !== undefined && String(tenantId).trim()) {
    params.set("tenant_id", String(tenantId));
  }
  const query = params.size > 0 ? `?${params.toString()}` : "";
  const response = await fetch(
    apiPath(
      `/api/weknora/knowledge-bases/${knowledgeBaseId}/knowledge${query}`,
    ),
  );
  return readJson<WeKnoraListResponse<WeKnoraKnowledge>>(response);
}

export async function listAllWeKnoraKnowledge(
  knowledgeBaseId: string,
  tenantId?: string | number,
): Promise<WeKnoraListResponse<WeKnoraKnowledge>> {
  const pageSize = 100;
  const first = await listWeKnoraKnowledge(
    knowledgeBaseId,
    tenantId,
    1,
    pageSize,
  );
  const total = Math.max(first.total ?? first.items.length, first.items.length);
  if (first.items.length >= total) {
    return first;
  }

  const pages = Math.min(Math.ceil(total / pageSize), 20);
  const rest = await Promise.all(
    Array.from({ length: pages - 1 }, (_, index) =>
      listWeKnoraKnowledge(knowledgeBaseId, tenantId, index + 2, pageSize),
    ),
  );
  const items = [first, ...rest].flatMap((page) => page.items);
  return { ...first, items, total };
}

export async function listWeKnoraTags(
  knowledgeBaseId: string,
  tenantId?: string | number,
): Promise<WeKnoraListResponse<WeKnoraTag>> {
  const params = new URLSearchParams({ page: "1", page_size: "1000" });
  if (tenantId !== undefined && String(tenantId).trim()) {
    params.set("tenant_id", String(tenantId));
  }
  const response = await fetch(
    apiPath(
      `/api/weknora/knowledge-bases/${knowledgeBaseId}/tags?${params.toString()}`,
    ),
  );
  return readJson<WeKnoraListResponse<WeKnoraTag>>(response);
}

export async function searchWeKnoraKnowledge(
  keyword = "",
  offset = 0,
  limit = 100,
): Promise<WeKnoraListResponse<WeKnoraKnowledge>> {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  const normalizedKeyword = keyword.trim();
  if (normalizedKeyword) {
    params.set("keyword", normalizedKeyword);
  } else {
    params.set("recent", "true");
  }
  const response = await fetch(
    apiPath(`/api/weknora/knowledge/search?${params.toString()}`),
  );
  return readJson<WeKnoraListResponse<WeKnoraKnowledge>>(response);
}

export async function searchAllWeKnoraKnowledge(
  keyword = "",
  maxItems = 2_000,
): Promise<WeKnoraListResponse<WeKnoraKnowledge>> {
  const pageSize = 100;
  const first = await searchWeKnoraKnowledge(keyword, 0, pageSize);
  const total = Math.max(first.total ?? first.items.length, first.items.length);
  if (
    (!first.has_more && first.items.length >= total) ||
    maxItems <= pageSize
  ) {
    return first;
  }

  const maximumPages = Math.max(1, Math.ceil(maxItems / pageSize));
  const knownPages =
    total > first.items.length
      ? Math.min(Math.ceil(total / pageSize), maximumPages)
      : 1;
  const pages: WeKnoraListResponse<WeKnoraKnowledge>[] = [first];

  if (knownPages > 1) {
    pages.push(
      ...(await Promise.all(
        Array.from({ length: knownPages - 1 }, (_, index) =>
          searchWeKnoraKnowledge(keyword, (index + 1) * pageSize, pageSize),
        ),
      )),
    );
  } else {
    let page = first;
    for (let index = 1; index < maximumPages && page.has_more; index += 1) {
      page = await searchWeKnoraKnowledge(keyword, index * pageSize, pageSize);
      pages.push(page);
    }
  }

  const items: WeKnoraKnowledge[] = [];
  const seen = new Set<string>();
  for (const item of pages.flatMap((page) => page.items)) {
    const key = `${item.knowledge_base_id ?? ""}:${item.id}`;
    if (!seen.has(key)) {
      seen.add(key);
      items.push(item);
    }
  }

  return {
    ...first,
    items: items.slice(0, maxItems),
    total,
    has_more: items.length < total || pages.at(-1)?.has_more === true,
  };
}

export async function uploadWeKnoraKnowledgeFile(
  input: UploadWeKnoraKnowledgeFileInput,
): Promise<{ data?: WeKnoraKnowledge }> {
  const formData = new FormData();
  formData.append("file", input.file);
  const params = new URLSearchParams();
  if (input.tenantId !== undefined && String(input.tenantId).trim()) {
    params.set("tenant_id", String(input.tenantId));
  }
  const query = params.size > 0 ? `?${params.toString()}` : "";
  const response = await fetch(
    apiPath(
      `/api/weknora/knowledge-bases/${input.knowledgeBaseId}/knowledge/file${query}`,
    ),
    {
      method: "POST",
      body: formData,
    },
  );
  return readJson<{ data?: WeKnoraKnowledge }>(response);
}

export async function createWeKnoraManualKnowledge(
  input: CreateWeKnoraManualKnowledgeInput,
): Promise<{ data?: WeKnoraKnowledge }> {
  const params = new URLSearchParams();
  if (input.tenantId !== undefined && String(input.tenantId).trim()) {
    params.set("tenant_id", String(input.tenantId));
  }
  const query = params.size > 0 ? `?${params.toString()}` : "";
  const response = await fetch(
    apiPath(
      `/api/weknora/knowledge-bases/${input.knowledgeBaseId}/knowledge/manual${query}`,
    ),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: input.title,
        content: input.content,
        status: "publish",
      }),
    },
  );
  return readJson<{ data?: WeKnoraKnowledge }>(response);
}
