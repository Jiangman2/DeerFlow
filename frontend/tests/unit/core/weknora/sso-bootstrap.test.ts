import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

const patchRoot = resolve(process.cwd(), "../k8s/weknora/ui-patches");

test("WeKnora bootstrap persists SSO and applies the DeerFlow UI policy", () => {
  const html = readFileSync(resolve(patchRoot, "index.html"), "utf8");

  expect(html).toContain("/api/weknora/sso-session");
  expect(html).toContain('localStorage.setItem("weknora_token", data.token)');
  expect(html).toContain('"weknora_memberships"');
  expect(html).toContain("deerflow_weknora_ui_policy");
  expect(html).toContain("personal_workspace_owner");
  expect(html).toContain("installDeerFlowPolicyNavigationGuard");
  expect(html).toContain("allowedStandardSidebarGuides");
  expect(html).toContain('"nav-creatChat"');
  expect(html).toContain('"nav-knowledge-bases"');
  expect(html).toContain('"nav-organizations"');
  expect(html).toContain('"nav-agents"');
  expect(html).toContain('"/platform/agents"');
  expect(html).not.toContain('"/platform/integrations"');
  expect(html).not.toContain('          "nav-integrations",');
  expect(html).toContain('"Publish Integrations"');
  expect(html).toContain('[data-guide="nav-integrations"]');
  expect(html).toContain("item.hidden = true");
  expect(html).toContain('[data-guide^="nav-"]');
  expect(html).toContain("hideDisallowedStandardSidebarEntries");
  expect(html).toContain("hiddenStandardLabels");
  expect(html).toContain("hideStandardSettingsGroup");
  expect(html).toContain(
    ".settings-nav .nav-group-title, .settings-nav .nav-item",
  );
  expect(html).toContain(
    'const defaultChatModelId = "deerflow-cnooc-deepseek-v4-flash"',
  );
  expect(html).toContain('"weknora_last_chat_model_id",');
  expect(html).toContain("defaultChatModelId");
  expect(html).toContain("simplifyKnowledgeBaseEditor");
  expect(html).toContain("data-deerflow-kb-editor");
  expect(html).toContain('[data-guide="kb-editor-sidebar"]');
  expect(html).toContain("var firstPass =");
  expect(html).toContain("firstPass &&");
  expect(html).toContain("basicItem.click()");
  expect(html).toContain("--td-brand-color: #0868bd !important");
  expect(html).toContain("--brand-main: #0868bd !important");
  expect(html).toContain(".t-radio-button.t-is-checked");
  expect(html).toContain(".t-checkbox.t-is-checked .t-checkbox__input");
  expect(html).toContain(".t-switch.t-is-checked");
  expect(html).toContain(".indexing-check-item.is-checked");
  expect(html).toContain('[data-guide="kb-editor-nav-basic"]');
  expect(html).not.toContain(
    '[data-guide="kb-create-type"] .t-radio-button:not(:first-child)',
  );
  expect(html).not.toContain(
    '[data-guide="kb-create-indexing"] > .indexing-check-item:not(:first-child)',
  );
  expect(html).toContain('"sidebar_collapsed", "false"');
  expect(html).toContain("expandSidebarOnEntry");
  expect(html).toContain("sidebarExpansionApplied");
  expect(html).not.toContain("enforceCollapsedSidebar");
  expect(html).not.toContain("event.target.closest(\".sidebar-toggle-item\")");
  expect(html).not.toContain("event.target.closest(\".sidebar-drag-handle\")");
  expect(html).toContain('content: "\\77e5\\8bc6\\5e93"');
  expect(html).toContain(".user-dropdown");
  expect(html).toContain(".menu-item,");
  expect(html).not.toContain(".menu-item:not(.danger)");
  expect(html).not.toContain("installTenantSelectionGuard");
  expect(html).not.toContain("validateKnowledgeBaseRouteTenant");
  expect(html).not.toContain("Storage.prototype");
  expect(html).not.toContain("location.replace(");
  expect(html).not.toContain(
    "localStorage.setItem('weknora_selected_tenant_id'",
  );
});

test("auth adapter delegates tenant behavior to WeKnora", () => {
  const authModule = readFileSync(
    resolve(patchRoot, "index-DeX5ZUuT.js"),
    "utf8",
  );

  expect(authModule).toContain("/api/weknora/sso-session");
  expect(authModule).toContain(
    'return await get("/api/weknora-native/auth/me")',
  );
  expect(authModule).not.toContain('return await get("/api/v1/auth/me")');
  expect(authModule).not.toContain("reconcileSsoSession");
  expect(authModule).not.toContain("clearSelectedTenant");
  expect(authModule).not.toContain("weknora_selected_tenant_id");
  expect(authModule).not.toContain("weknora_knowledge_bases");
});
