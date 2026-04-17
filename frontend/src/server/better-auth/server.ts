import { headers } from "next/headers";
import { cache } from "react";

import { isDisabledUser } from "@/server/admin/db";

import { auth, ensureAuthMigrations } from ".";

export const getSession = cache(async () => {
  await ensureAuthMigrations();
  const s = await auth.api.getSession({ headers: await headers() });
  const userId = s?.user?.id;
  if (userId && isDisabledUser(userId)) return null;
  return s;
});
