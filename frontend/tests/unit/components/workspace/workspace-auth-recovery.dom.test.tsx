import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { useRouter } from "next/navigation";

import { WorkspaceAuthRecovery } from "@/components/workspace/workspace-auth-recovery";
import { useAuth } from "@/core/auth/AuthProvider";

rs.mock("next/navigation", () => ({ useRouter: rs.fn() }));
rs.mock("@/core/auth/AuthProvider", () => ({ useAuth: rs.fn() }));
rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      workspace: {
        gatewayUnavailable: "Service unavailable",
        gatewayUnavailableRetrying: "Retrying",
      },
    },
  }),
}));

afterEach(cleanup);

describe("workspace authentication recovery", () => {
  it("keeps the recovery screen while identity is unverified", () => {
    const refresh = rs.fn();
    rs.mocked(useRouter).mockReturnValue({
      refresh,
      bfcacheId: "test",
      back: rs.fn(),
      forward: rs.fn(),
      push: rs.fn(),
      replace: rs.fn(),
      prefetch: rs.fn(),
    });
    rs.mocked(useAuth).mockReturnValue({ user: null } as ReturnType<
      typeof useAuth
    >);
    render(<WorkspaceAuthRecovery />);
    expect(screen.getByRole("status").textContent).toContain(
      "Service unavailable",
    );
    expect(refresh).not.toHaveBeenCalled();
  });

  it("rechecks the server layout once a valid identity is recovered", () => {
    const refresh = rs.fn();
    rs.mocked(useRouter).mockReturnValue({
      refresh,
      bfcacheId: "test",
      back: rs.fn(),
      forward: rs.fn(),
      push: rs.fn(),
      replace: rs.fn(),
      prefetch: rs.fn(),
    });
    rs.mocked(useAuth).mockReturnValue({ user: { id: "reader" } } as ReturnType<
      typeof useAuth
    >);
    render(<WorkspaceAuthRecovery />);
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
