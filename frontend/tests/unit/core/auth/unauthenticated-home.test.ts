import { describe, expect, test } from "@rstest/core";

import {
  isSsoOnlyMode,
  resolveSsoOnlyAuthRedirect,
} from "@/core/auth/unauthenticated-home";

describe("enterprise SSO-only auth redirect", () => {
  test("recognizes explicit true-like runtime values only", () => {
    expect(isSsoOnlyMode("true")).toBe(true);
    expect(isSsoOnlyMode("1")).toBe(true);
    expect(isSsoOnlyMode("YES")).toBe(true);
    expect(isSsoOnlyMode("on")).toBe(true);
    expect(isSsoOnlyMode("false")).toBe(false);
    expect(isSsoOnlyMode(undefined)).toBe(false);
  });

  test("redirects every SSO-only auth page to the HTTPS data-lake portal", () => {
    expect(
      resolveSsoOnlyAuthRedirect("true", "https://datalake.cnooc/#/home"),
    ).toBe("https://datalake.cnooc/#/home");
  });

  test("keeps normal local auth behavior when SSO-only mode is disabled", () => {
    expect(
      resolveSsoOnlyAuthRedirect("false", "https://datalake.cnooc/#/home"),
    ).toBeNull();
  });

  test("fails closed to the local landing page when the portal URL is invalid", () => {
    expect(resolveSsoOnlyAuthRedirect("true", undefined)).toBe("/");
    expect(
      resolveSsoOnlyAuthRedirect("true", "http://datalake.cnooc/#/home"),
    ).toBe("/");
  });
});
