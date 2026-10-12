import { redirect } from "next/navigation";

import { GatewayOfflineFallback } from "@/components/workspace/gateway-offline-fallback";
import { WorkspaceAuthRecovery } from "@/components/workspace/workspace-auth-recovery";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { getServerSideUser } from "@/core/auth/server";
import { assertNever } from "@/core/auth/types";

import { WorkspaceContent } from "./workspace-content";

export const dynamic = "force-dynamic";

export default async function WorkspaceLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const result = await getServerSideUser();

  switch (result.tag) {
    case "authenticated":
      return (
        <AuthProvider initialUser={result.user}>
          <WorkspaceContent>{children}</WorkspaceContent>
        </AuthProvider>
      );
    case "needs_setup":
      redirect("/setup");
    case "system_setup_required":
      redirect("/setup");
    case "unauthenticated":
      redirect("/api/v1/auth/iam/login?next=%2Fworkspace%2Fchats%2Fnew");
    case "gateway_unavailable":
      // Failed authentication probes must not expose the workspace or its children.
      return (
        <GatewayOfflineFallback renderBanner>
          <WorkspaceAuthRecovery />
        </GatewayOfflineFallback>
      );
    case "config_error":
      throw new Error(result.message);
    default:
      assertNever(result);
  }
}
