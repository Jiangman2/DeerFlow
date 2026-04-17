import { redirect } from "next/navigation";

import { getSession } from "@/server/better-auth/server";

import WorkspaceLayoutClient from "./layout-client";

export default async function WorkspaceLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const s = await getSession();
  if (!s?.user) redirect("/login?next=/workspace");
  return (
    <WorkspaceLayoutClient user={{ name: s.user.name, email: s.user.email }}>
      {children}
    </WorkspaceLayoutClient>
  );
}
