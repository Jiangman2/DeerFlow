import { fetch as fetchWithAuth } from "@/core/api/fetcher";

export type DataLakeAuthorizationResult =
  | { ok: true }
  | { ok: false; status?: number; message?: string };

export async function ensureDataLakeAuthorization(): Promise<DataLakeAuthorizationResult> {
  try {
    const response = await fetchWithAuth("/api/v1/data-lake/auth/token", {
      method: "POST",
    });
    if (response.ok) {
      return { ok: true };
    }

    const payload = (await response.json().catch(() => null)) as {
      message?: unknown;
    } | null;
    return {
      ok: false,
      status: response.status,
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
