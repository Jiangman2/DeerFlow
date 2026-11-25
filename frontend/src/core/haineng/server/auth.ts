import type { User } from "@/core/auth/types";

export interface HainengAuthentication {
  headers: Readonly<Record<string, string>>;
}

/** Stage two extension point. Resolve credentials for THIS authenticated user.
 * Never read a shared personal SESSION or forward platform browser cookies.
 * Stage one intentionally calls upstream without credentials and reports its
 * authentication errors; it does not claim to implement Haineng SSO.
 */
export async function resolveHainengAuthentication(
  user: Pick<User, "id">,
): Promise<HainengAuthentication> {
  if (!user.id) throw new Error("Missing authenticated platform identity");
  return { headers: {} };
}
