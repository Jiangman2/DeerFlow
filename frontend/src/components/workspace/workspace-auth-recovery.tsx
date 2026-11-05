"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";

/** Refresh the server auth gate after recovery without rendering private children. */
export function WorkspaceAuthRecovery() {
  const { user } = useAuth();
  const { t } = useI18n();
  const router = useRouter();

  useEffect(() => {
    if (user !== null) router.refresh();
  }, [user, router]);

  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <p role="status" className="text-muted-foreground text-center">
        {t.workspace.gatewayUnavailable}{" "}
        {t.workspace.gatewayUnavailableRetrying}
      </p>
    </main>
  );
}
