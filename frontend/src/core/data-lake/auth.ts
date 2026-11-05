import { fetch as fetchWithAuth } from "@/core/api/fetcher";

export type DataLakeAuthorizationResult =
  | { ok: true }
  | { ok: false; status?: number; code?: string; message?: string };

export async function ensureDataLakeAuthorization(
  signal?: AbortSignal,
): Promise<DataLakeAuthorizationResult> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) controller.abort();
  const checkActive = () => {
    if (controller.signal.aborted) throw new Error("Authorization aborted");
  };
  // Token acquisition, response parsing and the fresh cookie check share one deadline.
  const timeout = setTimeout(() => controller.abort(), 60_000);
  try {
    checkActive();
    const response = await fetchWithAuth(
      "/api/v1/data-lake/auth/token",
      { method: "POST", signal: controller.signal },
      { redirectOnUnauthorized: false },
    );
    const payload = (await response.json().catch(() => null)) as {
      code?: unknown;
      message?: unknown;
    } | null;
    checkActive();
    if (response.ok) {
      if (
        payload?.code !== "authorized" &&
        payload?.code !== "already_authorized"
      ) {
        return { ok: false, code: "data_lake_authorization_invalid_response" };
      }
      const session = await fetchWithAuth(
        "/api/v1/data-lake/auth/session",
        { method: "GET", cache: "no-store", signal: controller.signal },
        { redirectOnUnauthorized: false },
      );
      const confirmation = (await session.json().catch(() => null)) as {
        authorized?: unknown;
      } | null;
      checkActive();
      return session.ok && confirmation?.authorized === true
        ? { ok: true }
        : {
            ok: false,
            status: session.status,
            code: "data_lake_session_unconfirmed",
          };
    }
    return {
      ok: false,
      status: response.status,
      ...(typeof payload?.code === "string" ? { code: payload.code } : {}),
      message:
        typeof payload?.message === "string" ? payload.message : undefined,
    };
  } catch (error) {
    if (controller.signal.aborted) {
      return {
        ok: false,
        code: signal?.aborted
          ? "data_lake_authorization_cancelled"
          : "data_lake_authorization_timeout",
      };
    }
    return {
      ok: false,
      message: error instanceof Error ? error.message : undefined,
    };
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", cancel);
  }
}
