export const CNOOC_PLATFORM_TOKEN_COOKIE_NAME = "cnooc.platform_token";

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
