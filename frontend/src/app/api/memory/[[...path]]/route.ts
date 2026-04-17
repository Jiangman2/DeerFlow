import { NextResponse, type NextRequest } from "next/server";

import { env } from "@/env";
import { getSession } from "@/server/better-auth/server";
import { buildProxyHeaders } from "@/server/df-proxy";

function gatewayBaseUrl() {
  return env.DEERFLOW_GATEWAY_BASE_URL ?? "http://127.0.0.1:8001";
}

async function requireUser() {
  const s = await getSession();
  const userId = s?.user?.id;
  return userId ?? null;
}

function upstreamUrl(request: NextRequest, path: string[] | undefined) {
  const joined = (path ?? []).join("/");
  const upstreamPath = joined ? `/api/memory/${joined}` : `/api/memory`;
  const url = new URL(upstreamPath, gatewayBaseUrl());
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));
  return url;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const userId = await requireUser();
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const url = upstreamUrl(request, (await params).path);
  const r = await fetch(url, { headers: buildProxyHeaders(userId) });
  return new Response(r.body, { status: r.status, headers: r.headers });
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const userId = await requireUser();
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const url = upstreamUrl(request, (await params).path);
  const body = await request.arrayBuffer();
  const r = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": request.headers.get("content-type") ?? "application/json",
      ...buildProxyHeaders(userId),
    },
    body,
  });
  return new Response(r.body, { status: r.status, headers: r.headers });
}

