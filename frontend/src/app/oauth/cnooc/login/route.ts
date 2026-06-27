import { NextResponse, type NextRequest } from "next/server";

function validateNextParam(value: string | null): string {
  if (!value) return "/workspace/chats/new";
  if (!value.startsWith("/") || value.startsWith("//") || value.includes(":")) {
    return "/workspace/chats/new";
  }
  return value;
}

export function GET(request: NextRequest) {
  const token = request.nextUrl.searchParams.get("token")?.trim();
  if (!token) {
    return NextResponse.redirect(new URL("/login?error=sso_failed", request.url));
  }

  const loginUrl = new URL("/api/v1/auth/cnooc/login", request.url);
  loginUrl.searchParams.set("token", token);
  loginUrl.searchParams.set("next", validateNextParam(request.nextUrl.searchParams.get("next")));
  return NextResponse.redirect(loginUrl);
}
