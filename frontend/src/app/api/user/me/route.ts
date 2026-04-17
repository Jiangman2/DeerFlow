import { NextResponse } from "next/server";

import { getSession } from "@/server/better-auth/server";

export async function GET() {
  const s = await getSession();
  const id = s?.user?.id ?? null;
  return NextResponse.json({ userId: id });
}
