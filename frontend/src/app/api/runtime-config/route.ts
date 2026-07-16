import { NextResponse, type NextRequest } from "next/server";

export const dynamic = "force-dynamic";

function cleanUrl(value: string | undefined) {
  const trimmed = value?.trim();
  if (trimmed === undefined || trimmed.length === 0) {
    return null;
  }
  return trimmed;
}

function requestHostname(request: NextRequest) {
  const forwardedHost = request.headers.get("x-forwarded-host")?.split(",")[0];
  const host = forwardedHost ?? request.headers.get("host");
  return host?.split(":")[0]?.trim() ?? null;
}

function alignLocalhostUrl(value: string | null, request: NextRequest) {
  if (!value) return null;
  const host = requestHostname(request);
  if (!host || !["localhost", "127.0.0.1"].includes(host)) return value;

  try {
    const url = new URL(value);
    if (["localhost", "127.0.0.1"].includes(url.hostname)) {
      url.hostname = host;
      return url.toString().replace(/\/$/, "");
    }
  } catch {
    return value;
  }
  return value;
}

function withWeKnoraKnowledgeBasePath(value: string | null) {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.pathname === "/" || url.pathname === "") {
      url.pathname = "/platform/knowledge-bases";
    }
    return url.toString().replace(/\/$/, "");
  } catch {
    return value;
  }
}

export async function GET(request: NextRequest) {
  const weknoraFrontendUrl =
    cleanUrl(process.env.WEKNORA_FRONTEND_URL) ??
    cleanUrl(process.env.NEXT_PUBLIC_WEKNORA_FRONTEND_URL);

  return NextResponse.json({
    weknoraFrontendUrl: withWeKnoraKnowledgeBasePath(
      alignLocalhostUrl(weknoraFrontendUrl, request),
    ),
  });
}
