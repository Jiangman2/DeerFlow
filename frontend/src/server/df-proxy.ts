import crypto from "node:crypto";

import { env } from "@/env";

function proxySecret() {
  return env.DEERFLOW_PROXY_SECRET ?? env.BETTER_AUTH_SECRET ?? "";
}

export function buildProxyHeaders(userId: string) {
  const ts = Math.floor(Date.now() / 1000).toString();
  const secret = proxySecret();
  const sig = crypto
    .createHmac("sha256", secret)
    .update(`${userId}.${ts}`)
    .digest("base64url");
  return {
    "x-df-user": userId,
    "x-df-ts": ts,
    "x-df-sig": sig,
  };
}

