import { NextResponse } from "next/server";

import { isAdminUser, listManagedUsers } from "@/server/admin/db";
import { ensureAuthMigrations } from "@/server/better-auth";
import { getSession } from "@/server/better-auth/server";

export async function GET() {
  await ensureAuthMigrations();
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });
  if (!isAdminUser(userId)) return NextResponse.json({ detail: "forbidden" }, { status: 403 });

  const users = listManagedUsers();
  return NextResponse.json({ users });
}
