import { NextResponse } from "next/server";

import { getServerSideUser } from "@/core/auth/server";
import {
  buildHainengAuthHeaders,
  DEFAULT_HAINENG_MODEL_CALL_COUNT_URL,
  parseModelCallCount,
} from "@/server/haineng/model-call-count";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const NO_STORE = { "Cache-Control": "private, no-store" };

export async function GET() {
  const auth = await getServerSideUser();
  if (auth.tag !== "authenticated") {
    return NextResponse.json(
      { error: "unauthorized" },
      { status: 401, headers: NO_STORE },
    );
  }

  const endpoint =
    process.env.HAINENG_MODEL_CALL_COUNT_URL?.trim() ??
    DEFAULT_HAINENG_MODEL_CALL_COUNT_URL;
  const appId = process.env.HAINENG_MODEL_CALL_COUNT_APP_ID?.trim();
  const appKey = process.env.HAINENG_MODEL_CALL_COUNT_APP_KEY?.trim();
  if (!appId || !appKey) {
    return NextResponse.json(
      { error: "not_configured" },
      { status: 503, headers: NO_STORE },
    );
  }

  let url: URL;
  try {
    url = new URL(endpoint);
    if (url.protocol !== "https:" || url.username || url.password) {
      throw new Error("An HTTPS endpoint without URL credentials is required");
    }
  } catch {
    return NextResponse.json(
      { error: "invalid_configuration" },
      { status: 503, headers: NO_STORE },
    );
  }

  try {
    const response = await fetch(url, {
      method: "GET",
      headers: {
        ...buildHainengAuthHeaders(appId, appKey, Date.now()),
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      // Do not forward signed headers to a different origin via redirects.
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) {
      return NextResponse.json(
        {
          error:
            response.status === 401 || response.status === 403
              ? "upstream_auth_failed"
              : "upstream_unavailable",
          upstreamStatus: response.status,
        },
        { status: 502, headers: NO_STORE },
      );
    }

    const payload: unknown = await response.json();
    if (
      typeof payload === "object" &&
      payload !== null &&
      "code" in payload &&
      (payload.code === 401 || payload.code === 403)
    ) {
      return NextResponse.json(
        { error: "upstream_auth_failed", upstreamCode: payload.code },
        { status: 502, headers: NO_STORE },
      );
    }
    const counts = parseModelCallCount(payload);
    if (!counts) {
      return NextResponse.json(
        { error: "invalid_upstream_response" },
        { status: 502, headers: NO_STORE },
      );
    }
    return NextResponse.json(counts, { headers: NO_STORE });
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      return NextResponse.json(
        { error: "upstream_timeout" },
        { status: 504, headers: NO_STORE },
      );
    }
    return NextResponse.json(
      { error: "upstream_unavailable" },
      { status: 502, headers: NO_STORE },
    );
  }
}
