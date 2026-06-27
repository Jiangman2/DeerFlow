import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  CreateUserInput,
  CreateUserResponse,
  ManagedUser,
  ResetPasswordInput,
  ResetPasswordResponse,
  UpdateUserInput,
  UserListResponse,
} from "./types";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function listManagedUsers(q?: string): Promise<UserListResponse> {
  const params = new URLSearchParams({ limit: "200" });
  if (q?.trim()) params.set("q", q.trim());
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/users?${params}`,
  );
  return readJson<UserListResponse>(response);
}

export async function createManagedUser(
  input: CreateUserInput,
): Promise<CreateUserResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/admin/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return readJson<CreateUserResponse>(response);
}

export async function updateManagedUser(
  userId: string,
  input: UpdateUserInput,
): Promise<ManagedUser> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/users/${encodeURIComponent(userId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  const data = await readJson<{ item: ManagedUser }>(response);
  return data.item;
}

export async function resetManagedUserPassword(
  userId: string,
  input: ResetPasswordInput,
): Promise<ResetPasswordResponse> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/users/${encodeURIComponent(
      userId,
    )}/reset-password`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  return readJson<ResetPasswordResponse>(response);
}

export async function deleteManagedUser(
  userId: string,
  deleteFileData: boolean,
): Promise<void> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/users/${encodeURIComponent(userId)}`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delete_file_data: deleteFileData }),
    },
  );
  await readJson<{ success: boolean }>(response);
}
