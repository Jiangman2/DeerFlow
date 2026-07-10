export const CNOOC_PLATFORM_TOKEN_COOKIE_NAME = "cnooc.platform_token";

export function buildCnoocPlatformTokenCookieOptions(secure: boolean) {
  return {
    httpOnly: true,
    maxAge: 60 * 60 * 24 * 7,
    path: "/",
    sameSite: "lax" as const,
    secure,
  };
}

export function resolveCnoocPlatformToken(
  cookieToken: string | null | undefined,
  fallbackToken: string | null | undefined,
) {
  const normalizedCookieToken = cookieToken?.trim();
  if (normalizedCookieToken) return normalizedCookieToken;

  const normalizedFallbackToken = fallbackToken?.trim();
  if (normalizedFallbackToken) return normalizedFallbackToken;

  return null;
}
