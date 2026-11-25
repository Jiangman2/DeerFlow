import type { User } from "@/core/auth/types";

export interface HainengAuthentication {
  headers: Readonly<Record<string, string>>;
}

/** Temporary opt-in test credentials, bound to ONE authenticated platform user.
 * Stage two will replace this branch with per-user UAP credential resolution.
 * Never forward platform cookies, log credentials, or use a global fallback.
 */
export async function resolveHainengAuthentication(
  user: Pick<User, "id">,
): Promise<HainengAuthentication> {
  if (!user.id) throw new Error("Missing authenticated platform identity");
  if (process.env.HAINENG_TEST_AUTH_ENABLED !== "true") return { headers: {} };
  const ownerId = process.env.HAINENG_SESSION_USER_ID?.trim();
  if (!ownerId || user.id !== ownerId) return { headers: {} };
  const session = process.env.HAINENG_SESSION;
  if (!session?.trim()) return { headers: {} };
  // RFC 6265 cookie-octet: no whitespace, CR/LF, separators or header injection.
  if (!/^[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]+$/.test(session)) {
    throw new Error("Invalid temporary Haineng credential format");
  }
  return { headers: { Cookie: `SESSION=${session}` } };
}
