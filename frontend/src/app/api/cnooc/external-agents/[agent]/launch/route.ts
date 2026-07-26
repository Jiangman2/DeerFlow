import { type NextRequest, NextResponse } from "next/server";

import {
  buildCnoocExternalAgentTargetUrl,
  isCnoocExternalAgentKey,
} from "@/server/cnooc-auth/external-agent-launch";
import {
  CNOOC_PLATFORM_TOKEN_COOKIE_NAME,
  resolveCnoocPlatformToken,
} from "@/server/cnooc-auth/platform-token";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const RESPONSE_SECURITY_HEADERS = {
  "Cache-Control": "private, no-store, max-age=0",
  Pragma: "no-cache",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex, nofollow",
} as const;

function applySecurityHeaders(response: NextResponse) {
  for (const [name, value] of Object.entries(RESPONSE_SECURITY_HEADERS)) {
    response.headers.set(name, value);
  }
  return response;
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ agent: string }> },
) {
  const { agent } = await context.params;
  if (!isCnoocExternalAgentKey(agent)) {
    return applySecurityHeaders(
      NextResponse.json(
        { detail: "Unknown external agent." },
        { status: 404 },
      ),
    );
  }

  const platformToken = resolveCnoocPlatformToken(
    request.cookies.get(CNOOC_PLATFORM_TOKEN_COOKIE_NAME)?.value,
    null,
  );
  if (!platformToken) {
    return applySecurityHeaders(
      NextResponse.json(
        {
          detail:
            "The CNOOC platform SSO token is unavailable. Re-enter DeerFlow from the data-lake portal.",
        },
        { status: 401 },
      ),
    );
  }

  let target: URL;
  try {
    target = buildCnoocExternalAgentTargetUrl(agent, platformToken);
  } catch {
    return applySecurityHeaders(
      NextResponse.json(
        { detail: "The CNOOC platform SSO token is invalid." },
        { status: 401 },
      ),
    );
  }

  return applySecurityHeaders(NextResponse.redirect(target, 302));
}
