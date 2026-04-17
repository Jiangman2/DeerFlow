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

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ thread_id: string; path?: string[] }> },
) {
  const userId = await requireUser();
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const { thread_id, path } = await params;
  const joined = (path ?? []).join("/");
  const upstreamPath = joined
    ? `/api/threads/${encodeURIComponent(thread_id)}/uploads/${joined}`
    : `/api/threads/${encodeURIComponent(thread_id)}/uploads`;
  const url = new URL(
    upstreamPath,
    gatewayBaseUrl(),
  );
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));

  const r = await fetch(url, { headers: buildProxyHeaders(userId) });
  return new Response(r.body, { status: r.status, headers: r.headers });
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ thread_id: string; path?: string[] }> },
) {
  const userId = await requireUser();
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const { thread_id, path } = await params;
  const joined = (path ?? []).join("/");
  const upstreamPath = joined
    ? `/api/threads/${encodeURIComponent(thread_id)}/uploads/${joined}`
    : `/api/threads/${encodeURIComponent(thread_id)}/uploads`;
  const url = new URL(
    upstreamPath,
    gatewayBaseUrl(),
  );
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));

  const body = await request.arrayBuffer();
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const proxyHeaders = buildProxyHeaders(userId);
  Object.entries(proxyHeaders).forEach(([k, v]) => headers.set(k, v));

  const r = await fetch(url, { method: "POST", headers, body });
  return new Response(r.body, { status: r.status, headers: r.headers });
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ thread_id: string; path?: string[] }> },
) {
  const userId = await requireUser();
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const { thread_id, path } = await params;
  const joined = (path ?? []).join("/");
  const upstreamPath = joined
    ? `/api/threads/${encodeURIComponent(thread_id)}/uploads/${joined}`
    : `/api/threads/${encodeURIComponent(thread_id)}/uploads`;
  const url = new URL(
    upstreamPath,
    gatewayBaseUrl(),
  );
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));

  const r = await fetch(url, { method: "DELETE", headers: buildProxyHeaders(userId) });
  return new Response(r.body, { status: r.status, headers: r.headers });
}
