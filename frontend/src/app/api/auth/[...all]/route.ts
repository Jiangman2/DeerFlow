import { toNextJsHandler } from "better-auth/next-js";

import { getAllowSignup } from "@/server/admin/db";
import { auth } from "@/server/better-auth";
import { ensureAuthMigrations } from "@/server/better-auth";

const { GET: baseGET, POST: basePOST } = toNextJsHandler(auth.handler);

export async function GET(request: Request) {
  await ensureAuthMigrations();
  return baseGET(request);
}

export async function POST(request: Request) {
  await ensureAuthMigrations();
  const url = new URL(request.url);
  if (url.pathname.includes("/sign-up") && !getAllowSignup()) {
    return new Response(JSON.stringify({ detail: "sign up disabled" }), {
      status: 403,
      headers: { "content-type": "application/json" },
    });
  }
  return basePOST(request);
}
