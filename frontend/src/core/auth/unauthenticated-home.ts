import { type AuthResult } from "./types";

export function resolveUnauthenticatedHomeRedirect(
  authResult: AuthResult,
  configuredUrl: string | undefined,
): string | null {
  if (authResult.tag !== "unauthenticated") {
    return null;
  }

  const candidate = configuredUrl?.trim();
  if (!candidate) {
    return null;
  }

  try {
    const target = new URL(candidate);
    return target.protocol === "https:" ? target.toString() : null;
  } catch {
    return null;
  }
}
