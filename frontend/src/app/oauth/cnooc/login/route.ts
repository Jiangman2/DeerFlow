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
    return new NextResponse(null, {
      status: 302,
      headers: { Location: "/login?error=sso_failed" },
    });
  }

  const loginUrl = new URL("http://internal.local/api/v1/auth/cnooc/login");
  loginUrl.searchParams.set("token", token);
  loginUrl.searchParams.set(
    "next",
    validateNextParam(request.nextUrl.searchParams.get("next")),
  );
  return new NextResponse(null, {
    status: 302,
    headers: { Location: `${loginUrl.pathname}${loginUrl.search}` },
  });
}
