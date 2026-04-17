import { NextResponse, type NextRequest } from "next/server";

import { env } from "@/env";
import { getSession } from "@/server/better-auth/server";
import { buildProxyHeaders } from "@/server/df-proxy";

function gatewayBaseUrl() {
  return env.DEERFLOW_GATEWAY_BASE_URL ?? "http://127.0.0.1:8001";
}

export async function GET(request: NextRequest) {
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const url = new URL("/api/threads", gatewayBaseUrl());
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));

  const r = await fetch(url, {
    headers: buildProxyHeaders(userId),
  });
  const text = await r.text();
  return new Response(text, { status: r.status, headers: r.headers });
}

export async function POST(request: NextRequest) {
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const url = new URL("/api/threads", gatewayBaseUrl());
  const body = await request.text();
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...buildProxyHeaders(userId) },
    body,
  });
  const text = await r.text();
  return new Response(text, { status: r.status, headers: r.headers });
}
