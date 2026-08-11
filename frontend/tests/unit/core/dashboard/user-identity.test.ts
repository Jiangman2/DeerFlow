import { describe, expect, it } from "@rstest/core";

import {
  dashboardAccountLabel,
  dashboardUserPrimaryLabel,
  dashboardUserSecondaryParts,
} from "@/core/dashboard/user-identity";

describe("dashboard user identity", () => {
  it("shows the SSO display name before the login account", () => {
    const user = {
      user_id: "user-1",
      email: "zhangsan@cnooc.com.cn",
      display_name: "张三",
      organization_name: "勘探开发研究室",
    };

    expect(dashboardUserPrimaryLabel(user)).toBe("张三");
    expect(dashboardUserSecondaryParts(user)).toEqual([
      "登录账号：zhangsan@cnooc.com.cn",
      "组织机构：勘探开发研究室",
    ]);
  });

  it("falls back safely for local accounts without SSO profile fields", () => {
    const user = {
      user_id: "local-user",
      email: "local@example.com",
      display_name: null,
      organization_name: null,
    };

    expect(dashboardUserPrimaryLabel(user)).toBe("local@example.com");
    expect(dashboardUserSecondaryParts(user)).toEqual([
      "登录账号：local@example.com",
    ]);
  });
  it("shows both a distinct display name and account for inventory rows", () => {
    expect(dashboardAccountLabel("Zhang San", "zhangsan@example.com")).toBe(
      "Zhang San · zhangsan@example.com",
    );
    expect(dashboardAccountLabel("same@example.com", "same@example.com")).toBe(
      "same@example.com",
    );
  });

  it("falls back when an inventory identity is missing", () => {
    expect(dashboardAccountLabel(null, undefined, "unknown-id")).toBe(
      "unknown-id",
    );
  });
});
