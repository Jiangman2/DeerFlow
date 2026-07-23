import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  CreateWeKnoraKnowledgeBaseInput,
  CreateWeKnoraManualKnowledgeInput,
  UploadWeKnoraKnowledgeFileInput,
  WeKnoraKnowledge,
  WeKnoraKnowledgeBase,
  WeKnoraListResponse,
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
): Promise<WeKnoraListResponse<WeKnoraKnowledge>> {
  const params = new URLSearchParams();
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
