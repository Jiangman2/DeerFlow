import { describe, expect, it } from "@rstest/core";

import {
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
});
