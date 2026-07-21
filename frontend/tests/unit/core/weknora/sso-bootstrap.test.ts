import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

const patchRoot = resolve(process.cwd(), "../k8s/weknora/ui-patches");

test("WeKnora bootstrap is limited to SSO persistence", () => {
  const html = readFileSync(resolve(patchRoot, "index.html"), "utf8");

  expect(html).toContain("/api/weknora/sso-session");
  expect(html).toContain("localStorage.setItem('weknora_token', data.token)");
  expect(html).toContain("localStorage.setItem('weknora_memberships'");
  expect(html).not.toContain("installTenantSelectionGuard");
  expect(html).not.toContain("validateKnowledgeBaseRouteTenant");
  expect(html).not.toContain("Storage.prototype");
  expect(html).not.toContain("location.replace(");
  expect(html).not.toContain("localStorage.setItem('weknora_selected_tenant_id'");
});

test("auth adapter delegates tenant behavior to WeKnora", () => {
  const authModule = readFileSync(resolve(patchRoot, "index-DeX5ZUuT.js"), "utf8");

  expect(authModule).toContain("/api/weknora/sso-session");
  expect(authModule).toContain('return await get("/api/weknora-native/auth/me")');
  expect(authModule).not.toContain('return await get("/api/v1/auth/me")');
  expect(authModule).not.toContain("reconcileSsoSession");
  expect(authModule).not.toContain("clearSelectedTenant");
  expect(authModule).not.toContain("weknora_selected_tenant_id");
  expect(authModule).not.toContain("weknora_knowledge_bases");
});
