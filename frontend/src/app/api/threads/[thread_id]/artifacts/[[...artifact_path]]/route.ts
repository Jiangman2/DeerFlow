import { NextResponse, type NextRequest } from "next/server";

import { env } from "@/env";
import { getSession } from "@/server/better-auth/server";
import { buildProxyHeaders } from "@/server/df-proxy";

function gatewayBaseUrl() {
  return env.DEERFLOW_GATEWAY_BASE_URL ?? "http://127.0.0.1:8001";
}

export async function GET(
  request: NextRequest,
  {
    params,
  }: {
    params: Promise<{ thread_id: string; artifact_path?: string[] }>;
  },
) {
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const { thread_id, artifact_path } = await params;
  const joined = (artifact_path ?? []).join("/");
  const upstreamPath = joined
    ? `/api/threads/${encodeURIComponent(thread_id)}/artifacts/${joined}`
    : `/api/threads/${encodeURIComponent(thread_id)}/artifacts`;
  const url = new URL(
    upstreamPath,
    gatewayBaseUrl(),
  );
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));

  const r = await fetch(url, { headers: buildProxyHeaders(userId) });
  return new Response(r.body, { status: r.status, headers: r.headers });
}
