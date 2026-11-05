import { type NextRequest, NextResponse } from "next/server";

export const CNOOC_PLATFORM_TOKEN_COOKIE_NAME = "cnooc.platform_token";

export type CnoocPlatformTokenRejectionReason = "invalid" | "unavailable";

export type CnoocPlatformTokenAuthorization =
  | { authorized: true; token: string }
  | { authorized: false; reason: CnoocPlatformTokenRejectionReason };

function getInternalGatewayBaseUrl() {
  const internalUrl = process.env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL?.trim();
  if (internalUrl) return internalUrl;
  const publicUrl = process.env.NEXT_PUBLIC_BACKEND_BASE_URL?.trim();
  if (publicUrl) return publicUrl;
  return "http://127.0.0.1:8001";
}

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
) {
  const normalizedCookieToken = cookieToken?.trim();
  if (normalizedCookieToken) return normalizedCookieToken;
  return null;
}

export async function authorizeCnoocPlatformToken(
  request: NextRequest,
): Promise<CnoocPlatformTokenAuthorization> {
  const token = resolveCnoocPlatformToken(
    request.cookies.get(CNOOC_PLATFORM_TOKEN_COOKIE_NAME)?.value,
  );
  if (!token) {
    return { authorized: false, reason: "invalid" };
  }

  const cookie = request.headers.get("cookie") ?? "";
  const sessionUrl = new URL(
    "/api/v1/data-lake/auth/session",
    getInternalGatewayBaseUrl(),
  );

  let response: Response;
  try {
    response = await fetch(sessionUrl, {
      cache: "no-store",
      headers: { cookie },
      method: "GET",
      redirect: "manual",
    });
  } catch {
    return { authorized: false, reason: "unavailable" };
  }

  if (response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    if (
      typeof payload === "object" &&
      payload !== null &&
      "authorized" in payload &&
      payload.authorized === true
    ) {
      return { authorized: true, token };
    }
    return { authorized: false, reason: "unavailable" };
  }

  if (response.status === 401 || response.status === 403) {
    return { authorized: false, reason: "invalid" };
  }
  return { authorized: false, reason: "unavailable" };
}

export function buildCnoocPlatformTokenRejection(
  reason: CnoocPlatformTokenRejectionReason,
) {
  const response = NextResponse.json(
    {
      detail:
        reason === "invalid"
          ? "Data Lake authorization is invalid. Reauthorize before continuing."
          : "Data Lake authorization validation is temporarily unavailable.",
    },
    { status: reason === "invalid" ? 401 : 503 },
  );
  response.headers.set("Cache-Control", "private, no-store, max-age=0");
  response.headers.set("Referrer-Policy", "no-referrer");
  if (reason === "invalid") {
    response.cookies.set(CNOOC_PLATFORM_TOKEN_COOKIE_NAME, "", {
      httpOnly: true,
      maxAge: 0,
      path: "/",
      sameSite: "lax",
    });
  }
  return response;
}
