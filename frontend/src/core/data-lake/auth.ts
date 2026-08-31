import { fetch as fetchWithAuth } from "@/core/api/fetcher";

export type DataLakeAuthorizationResult =
  | { ok: true }
  | { ok: false; status?: number; code?: string; message?: string };

export async function ensureDataLakeAuthorization(): Promise<DataLakeAuthorizationResult> {
  try {
    const response = await fetchWithAuth(
      "/api/v1/data-lake/auth/token",
      { method: "POST" },
      { redirectOnUnauthorized: false },
    );
    if (response.ok) {
      return { ok: true };
    }

    const payload = (await response.json().catch(() => null)) as {
      code?: unknown;
      message?: unknown;
    } | null;
    return {
      ok: false,
      status: response.status,
      ...(typeof payload?.code === "string" ? { code: payload.code } : {}),
      message:
        typeof payload?.message === "string" ? payload.message : undefined,
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : undefined,
    };
  }
}
