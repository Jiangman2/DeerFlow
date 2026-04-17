import { NextResponse } from "next/server";

import { getAllowSignup } from "@/server/admin/db";
import { ensureAuthMigrations } from "@/server/better-auth";

export async function GET() {
  await ensureAuthMigrations();
  return NextResponse.json({ allowSignup: getAllowSignup() });
}
