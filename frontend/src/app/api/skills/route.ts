import { NextResponse } from "next/server";

import { env } from "@/env";
import { getSession } from "@/server/better-auth/server";
import { buildProxyHeaders } from "@/server/df-proxy";

function gatewayBaseUrl() {
  return env.DEERFLOW_GATEWAY_BASE_URL ?? "http://127.0.0.1:8001";
}

export async function GET() {
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const url = new URL("/api/skills", gatewayBaseUrl());
  const r = await fetch(url, { headers: buildProxyHeaders(userId) });
  const text = await r.text();
  return new Response(text, { status: r.status, headers: r.headers });
}

