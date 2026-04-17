import { NextResponse, type NextRequest } from "next/server";

export default function proxy(request: NextRequest) {
  const host = request.headers.get("host") ?? "";
  if (host.startsWith("127.0.0.1")) {
    const url = request.nextUrl.clone();
    url.hostname = "localhost";
    url.port = "3000";
    return NextResponse.redirect(url, 307);
  }
  if (request.nextUrl.pathname === "/@vite/client") {
    return new NextResponse(null, { status: 204 });
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
