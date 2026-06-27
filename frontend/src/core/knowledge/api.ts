import { fetch } from "../api/fetcher";
import { getBackendBaseURL } from "../config";

import type {
  KnowledgeFile,
  KnowledgeFileCopyInput,
  KnowledgeFileMoveInput,
  KnowledgeFolder,
  KnowledgeFolderCreateInput,
  KnowledgeFolderDeleteResponse,
  KnowledgeFolderMoveInput,
  KnowledgeFolderRenameInput,
  KnowledgeListResponse,
  KnowledgeSearchResponse,
  KnowledgeUploadResponse,
} from "./types";

async function readJsonResponse<T>(
  response: Response,
  fallbackMessage: string,
): Promise<T> {
  if (!response.ok) {
    const errorData = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    const detail =
      typeof errorData.detail === "string"
        ? errorData.detail
        : JSON.stringify(errorData.detail ?? {});
    throw new Error(detail && detail !== "{}" ? detail : fallbackMessage);
  }
  return response.json() as Promise<T>;
}

export async function listKnowledgeFiles(): Promise<KnowledgeListResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/knowledge/files`);
  return readJsonResponse(response, "Failed to load knowledge files");
}

export async function uploadKnowledgeFiles(
  files: File[],
): Promise<KnowledgeUploadResponse> {
  const form = new FormData();
  for (const file of files) {
    const relativePath =
      (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
      file.name;
    form.append("files", file, relativePath);
  }
  const response = await fetch(`${getBackendBaseURL()}/api/knowledge/files`, {
    method: "POST",
    body: form,
  });
  return readJsonResponse(response, "Failed to upload knowledge files");
}

export async function deleteKnowledgeFolder(
  folderPath: string,
): Promise<KnowledgeFolderDeleteResponse> {
  const encodedPath = folderPath
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  const response = await fetch(
    `${getBackendBaseURL()}/api/knowledge/folders/${encodedPath}`,
    { method: "DELETE" },
  );
  return readJsonResponse(response, "Failed to delete knowledge folder");
}

export async function createKnowledgeFolder(
  input: KnowledgeFolderCreateInput,
): Promise<KnowledgeFolder> {
  const response = await fetch(`${getBackendBaseURL()}/api/knowledge/folders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return readJsonResponse(response, "Failed to create knowledge folder");
}

export async function moveKnowledgeFile(
  input: KnowledgeFileMoveInput,
): Promise<KnowledgeFile> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/knowledge/files/${encodeURIComponent(input.fileId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_folder_path: input.target_folder_path ?? "",
        target_filename: input.target_filename,
      }),
    },
  );
  return readJsonResponse(response, "Failed to move knowledge file");
}

export async function copyKnowledgeFile(
  input: KnowledgeFileCopyInput,
): Promise<KnowledgeFile> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/knowledge/files/${encodeURIComponent(input.fileId)}/copy`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_folder_path: input.target_folder_path ?? "",
        target_filename: input.target_filename,
      }),
    },
  );
  return readJsonResponse(response, "Failed to copy knowledge file");
}

export async function renameKnowledgeFolder(
  input: KnowledgeFolderRenameInput,
): Promise<KnowledgeFolder> {
  const encodedPath = input.folderPath
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  const response = await fetch(
    `${getBackendBaseURL()}/api/knowledge/folders/${encodedPath}/rename`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_name: input.new_name }),
    },
  );
  return readJsonResponse(response, "Failed to rename knowledge folder");
}

export async function moveKnowledgeFolder(
  input: KnowledgeFolderMoveInput,
): Promise<KnowledgeFolder> {
  const encodedPath = input.folderPath
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  const response = await fetch(
    `${getBackendBaseURL()}/api/knowledge/folders/${encodedPath}/move`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_path: input.target_path }),
    },
  );
  return readJsonResponse(response, "Failed to move knowledge folder");
}

export async function deleteKnowledgeFile(fileId: string): Promise<void> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/knowledge/files/${encodeURIComponent(fileId)}`,
    { method: "DELETE" },
  );
  await readJsonResponse(response, "Failed to delete knowledge file");
}

export async function reindexKnowledgeFile(
  fileId: string,
): Promise<KnowledgeFile> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/knowledge/files/${encodeURIComponent(fileId)}/reindex`,
    { method: "POST" },
  );
  return readJsonResponse(response, "Failed to reindex knowledge file");
}

export async function searchKnowledge(
  query: string,
  limit = 5,
  scopePaths: string[] = [],
): Promise<KnowledgeSearchResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/knowledge/search`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ query, limit, scope_paths: scopePaths }),
  });
  return readJsonResponse(response, "Failed to search knowledge");
}
