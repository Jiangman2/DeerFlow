import { NextResponse, type NextRequest } from "next/server";

import { env } from "@/env";
import { getSession } from "@/server/better-auth/server";
import { buildProxyHeaders } from "@/server/df-proxy";

function gatewayBaseUrl() {
  return env.DEERFLOW_GATEWAY_BASE_URL ?? "http://127.0.0.1:8001";
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const path = (await params).path.join("/");
  const url = new URL(`/api/langgraph/${path}`, gatewayBaseUrl());
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));

  const r = await fetch(url, { headers: buildProxyHeaders(userId) });
  return new Response(r.body, { status: r.status, headers: r.headers });
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const path = (await params).path.join("/");
  const url = new URL(`/api/langgraph/${path}`, gatewayBaseUrl());
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));

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

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const path = (await params).path.join("/");
  const url = new URL(`/api/langgraph/${path}`, gatewayBaseUrl());
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));

  const body = await request.arrayBuffer();
  const r = await fetch(url, {
    method: "PUT",
    headers: {
      "content-type": request.headers.get("content-type") ?? "application/json",
      ...buildProxyHeaders(userId),
    },
    body,
  });
  return new Response(r.body, { status: r.status, headers: r.headers });
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const path = (await params).path.join("/");
  const url = new URL(`/api/langgraph/${path}`, gatewayBaseUrl());
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));

  const body = await request.arrayBuffer();
  const r = await fetch(url, {
    method: "PATCH",
    headers: {
      "content-type": request.headers.get("content-type") ?? "application/json",
      ...buildProxyHeaders(userId),
    },
    body,
  });
  return new Response(r.body, { status: r.status, headers: r.headers });
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const path = (await params).path.join("/");
  const url = new URL(`/api/langgraph/${path}`, gatewayBaseUrl());
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));

  const r = await fetch(url, { method: "DELETE", headers: buildProxyHeaders(userId) });
  return new Response(r.body, { status: r.status, headers: r.headers });
}
