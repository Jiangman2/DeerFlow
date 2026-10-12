import { describe, expect, it, rs } from "@rstest/core";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import WorkspaceLayout from "@/app/workspace/layout";
import { getServerSideUser } from "@/core/auth/server";

rs.mock("next/navigation", () => ({ redirect: rs.fn() }));
rs.mock("@/core/auth/server", () => ({ getServerSideUser: rs.fn() }));
rs.mock("@/core/auth/AuthProvider", () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
}));
rs.mock("@/components/workspace/gateway-offline-fallback", () => ({
  GatewayOfflineFallback: ({ children }: { children: ReactNode }) => children,
}));
rs.mock("@/components/workspace/workspace-auth-recovery", () => ({
  WorkspaceAuthRecovery: () => <main>Authentication unavailable</main>,
}));
rs.mock("@/app/workspace/workspace-content", () => ({
  WorkspaceContent: ({ children }: { children: ReactNode }) => (
    <div>Private workspace menu{children}</div>
  ),
}));

describe("workspace authentication boundary", () => {
  it("does not render private menus or children when authentication is unavailable", async () => {
    rs.mocked(getServerSideUser).mockResolvedValue({
      tag: "gateway_unavailable",
    });
    const html = renderToStaticMarkup(
      await WorkspaceLayout({ children: <p>Private skill data</p> }),
    );
    expect(html).toContain("Authentication unavailable");
    expect(html).not.toContain("Private workspace menu");
    expect(html).not.toContain("Private skill data");
  });

  it("renders the workspace after successful server authentication", async () => {
    rs.mocked(getServerSideUser).mockResolvedValue({
      tag: "authenticated",
      user: {
        id: "reader",
        email: "reader@example.com",
        system_role: "user",
        needs_setup: false,
      },
    });
    const html = renderToStaticMarkup(
      await WorkspaceLayout({ children: <p>Private skill data</p> }),
    );
    expect(html).toContain("Private workspace menu");
    expect(html).toContain("Private skill data");
  });

  it("redirects anonymous users before rendering private content", async () => {
    rs.mocked(getServerSideUser).mockResolvedValue({ tag: "unauthenticated" });
    await WorkspaceLayout({ children: <p>Private skill data</p> });
    expect(redirect).toHaveBeenCalledWith(
      "/api/v1/auth/iam/login?next=%2Fworkspace%2Fchats%2Fnew",
    );
  });
});
