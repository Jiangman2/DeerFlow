import { NextResponse } from "next/server";

import { getAllowSignup, isAdminUser, setAllowSignup } from "@/server/admin/db";
import { ensureAuthMigrations } from "@/server/better-auth";
import { getSession } from "@/server/better-auth/server";

export async function GET() {
  await ensureAuthMigrations();
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });
  if (!isAdminUser(userId)) return NextResponse.json({ detail: "forbidden" }, { status: 403 });

  return NextResponse.json({ allowSignup: getAllowSignup() });
}

export async function PATCH(request: Request) {
  await ensureAuthMigrations();
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });
  if (!isAdminUser(userId)) return NextResponse.json({ detail: "forbidden" }, { status: 403 });

  const body = (await request.json().catch(() => null)) as { allowSignup?: boolean } | null;
  if (!body || typeof body.allowSignup !== "boolean") {
    return NextResponse.json({ detail: "invalid payload" }, { status: 400 });
  }
  setAllowSignup(body.allowSignup);
  return NextResponse.json({ success: true, allowSignup: getAllowSignup() });
}
