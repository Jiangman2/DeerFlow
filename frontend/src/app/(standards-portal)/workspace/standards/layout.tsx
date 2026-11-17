import { redirect } from "next/navigation";

import { getServerSideUser } from "@/core/auth/server";

export const dynamic = "force-dynamic";

export default async function StandardsLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const result = await getServerSideUser();

  if (result.tag !== "authenticated") {
    // The existing workspace owns login, setup, and authentication recovery.
    redirect("/workspace/chats/new");
  }

  return children;
}
