export interface DashboardUserIdentity {
  user_id: string;
  email: string | null;
  display_name: string | null;
  organization_name: string | null;
}

function normalized(value: string | null | undefined): string | null {
  const text = value?.trim();
  return text === undefined || text === "" ? null : text;
}

export function dashboardUserPrimaryLabel(user: DashboardUserIdentity): string {
  return (
    normalized(user.display_name) ??
    normalized(user.email) ??
    normalized(user.user_id) ??
    "未知用户"
  );
}

export function dashboardUserSecondaryParts(
  user: DashboardUserIdentity,
): string[] {
  const email = normalized(user.email);
  const organization = normalized(user.organization_name);
  return [
    ...(email ? [`登录账号：${email}`] : []),
    ...(organization ? [`组织机构：${organization}`] : []),
  ];
}
