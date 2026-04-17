import { NextResponse } from "next/server";

import { isAdminUser } from "@/server/admin/db";
import { ensureAuthMigrations } from "@/server/better-auth";
import { getSession } from "@/server/better-auth/server";

export async function GET() {
  await ensureAuthMigrations();
  const s = await getSession();
  const userId = s?.user?.id;
  if (!userId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });

  const isAdmin = isAdminUser(userId);
  if (!isAdmin) return NextResponse.json({ detail: "forbidden" }, { status: 403 });

  return NextResponse.json({
    isAdmin,
    user: {
      id: s.user.id,
      email: s.user.email ?? "",
      name: s.user.name ?? "",
    },
  });
}
