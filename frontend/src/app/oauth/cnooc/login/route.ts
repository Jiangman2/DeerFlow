import crypto from "node:crypto";

import { NextResponse } from "next/server";

import { createSsoSession, upsertSsoUser } from "@/server/admin/db";
import { auth, ensureAuthMigrations } from "@/server/better-auth";
import { checkCnoocToken, CnoocAuthError } from "@/server/cnooc-auth";
import {
  buildCnoocPlatformTokenCookieOptions,
  CNOOC_PLATFORM_TOKEN_COOKIE_NAME,
} from "@/server/cnooc-auth/platform-token";

import { buildCnoocLoginRedirectUrl } from "./route-helpers";

type BetterAuthCookieOptions = {
  advanced?: {
    cookiePrefix?: string;
    cookies?: {
      session_token?: {
        name?: string;
      };
    };
    useSecureCookies?: boolean;
  };
  baseURL?: string;
  secret?: string;
};

function getAuthOptions() {
  return auth.options as BetterAuthCookieOptions;
}

function shouldUseSecureAuthCookies() {
  const options = getAuthOptions();
  if (typeof options.advanced?.useSecureCookies === "boolean") {
    return options.advanced.useSecureCookies;
  }
  if (options.baseURL) return options.baseURL.startsWith("https://");
  return process.env.NODE_ENV === "production";
}

function getSessionCookieName() {
  const options = getAuthOptions();
  const prefix = options.advanced?.cookiePrefix ?? "better-auth";
  const name =
    options.advanced?.cookies?.session_token?.name ?? `${prefix}.session_token`;
  return shouldUseSecureAuthCookies() ? `__Secure-${name}` : name;
}

function getAuthSecret() {
  const secret = getAuthOptions().secret ?? process.env.BETTER_AUTH_SECRET;
  if (!secret) throw new Error("BETTER_AUTH_SECRET is required");
  return secret;
}

function signSessionCookieValue(value: string) {
  const hmac = crypto.createHmac("sha256", getAuthSecret());
  hmac.update(value);
  return `${value}.${hmac.digest("base64")}`;
}

export async function GET(request: Request) {
  await ensureAuthMigrations();

  const url = new URL(request.url);
  const token = url.searchParams.get("token") ?? "";
  if (!token) {
    return NextResponse.json({ detail: "token is required" }, { status: 400 });
  }

  try {
    const checked = await checkCnoocToken(token);
    upsertSsoUser({
      id: checked.user.deerflowUserId,
      name: checked.user.name,
      email: checked.user.email,
    });

    const session = createSsoSession(checked.user.deerflowUserId);
    const redirectUrl = buildCnoocLoginRedirectUrl(request.url);
    const response = NextResponse.redirect(redirectUrl);
    response.cookies.set(
      getSessionCookieName(),
      signSessionCookieValue(session.token),
      {
        expires: session.expiresAt,
        httpOnly: true,
        path: "/",
        sameSite: "lax",
        secure: shouldUseSecureAuthCookies(),
      },
    );
    response.cookies.set(
      CNOOC_PLATFORM_TOKEN_COOKIE_NAME,
      token,
      buildCnoocPlatformTokenCookieOptions(shouldUseSecureAuthCookies()),
    );
    return response;
  } catch (error) {
    const detail =
      error instanceof Error ? error.message : "cnooc login failed";
    const status = error instanceof CnoocAuthError ? error.status : 500;
    return NextResponse.json({ detail }, { status });
  }
}
