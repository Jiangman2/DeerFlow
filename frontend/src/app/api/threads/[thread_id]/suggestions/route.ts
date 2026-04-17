import { NextResponse, type NextRequest } from "next/server";

import { env } from "@/env";
import { getSession } from "@/server/better-auth/server";
import { buildProxyHeaders } from "@/server/df-proxy";

function gatewayBaseUrl() {
  return env.DEERFLOW_GATEWAY_BASE_URL ?? "http://127.0.0.1:8001";
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ thread_id: string }> },
) {
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const threadId = (await params).thread_id;
  const url = new URL(
    `/api/threads/${encodeURIComponent(threadId)}/suggestions`,
    gatewayBaseUrl(),
  );

  const body = await request.text();
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...buildProxyHeaders(userId) },
    body,
  });
  const text = await r.text();
  return new Response(text, { status: r.status, headers: r.headers });
}

