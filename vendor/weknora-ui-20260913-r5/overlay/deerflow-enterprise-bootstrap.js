const ENTERPRISE_VERSION = "weknora-v08-enterprise-sso2-domguard1";
const POLICY_KEY = "deerflow_weknora_ui_policy";
const DEFAULT_CHAT_MODEL_ID = "deerflow-cnooc-deepseek-v4-flash";
const DEFAULT_PATH = "/platform/knowledge-bases";
const SSO_PATH = "/api/weknora/sso-session";
const STANDARD_PATH_PREFIXES = [
  "/platform/creatChat",
  "/platform/chat/",
  "/platform/knowledge-bases",
  "/platform/organizations",
  "/platform/agents",
];

globalThis.__DEERFLOW_WEKNORA_ENTERPRISE_BOOTSTRAP_VERSION__ =
  ENTERPRISE_VERSION;

function readPolicy() {
  try {
    return JSON.parse(localStorage.getItem(POLICY_KEY) || "null");
  } catch (_) {
    return null;
  }
}

function isStandardPath(pathname) {
  return STANDARD_PATH_PREFIXES.some(
    (prefix) =>
      pathname === prefix ||
      pathname.startsWith(prefix.endsWith("/") ? prefix : `${prefix}/`),
  );
}

function enforcePolicyPath() {
  const policy = readPolicy();
  if (!policy || policy.full_ui !== false || isStandardPath(location.pathname)) {
    return;
  }
  const target = policy.default_path || DEFAULT_PATH;
  history.replaceState(history.state, "", target);
  window.dispatchEvent(new PopStateEvent("popstate", { state: history.state }));
}

function installNavigationGuard() {
  const policy = readPolicy();
  if (!policy || policy.full_ui !== false) return;

  if (!isStandardPath(location.pathname)) {
    history.replaceState(history.state, "", policy.default_path || DEFAULT_PATH);
  }

  for (const method of ["pushState", "replaceState"]) {
    const original = history[method].bind(history);
    history[method] = (...args) => {
      original(...args);
      queueMicrotask(enforcePolicyPath);
    };
  }
  window.addEventListener("popstate", () => queueMicrotask(enforcePolicyPath));
  document.addEventListener(
    "click",
    (event) => {
      const anchor =
        event.target instanceof Element
          ? event.target.closest("a[href]")
          : null;
      if (!anchor) return;
      const url = new URL(anchor.href, location.href);
      if (
        url.origin === location.origin &&
        url.pathname.startsWith("/platform/") &&
        !isStandardPath(url.pathname)
      ) {
        event.preventDefault();
        history.pushState({}, "", policy.default_path || DEFAULT_PATH);
      }
    },
    true,
  );
}

function clearTenantScopedState() {
  for (const key of [
    "weknora_selected_tenant_id",
    "weknora_selected_tenant_name",
    "weknora_knowledge_bases",
    "weknora_current_kb",
    "weknora_last_chat_model_id",
  ]) {
    localStorage.removeItem(key);
  }
}

async function validateRememberedTenant(token, activeTenant) {
  const selectedTenantId = String(
    localStorage.getItem("weknora_selected_tenant_id") || "",
  ).trim();
  if (!selectedTenantId || selectedTenantId === String(activeTenant.id)) return;

  try {
    const response = await fetch("/api/weknora-native/validate-tenant", {
      credentials: "include",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-ID": selectedTenantId,
      },
    });
    const payload = await response.json().catch(() => null);
    if (response.ok && payload?.valid === false) clearTenantScopedState();
  } catch (_) {
    // A transient validation failure must not destroy a valid tenant choice.
  }
}

async function bootstrapSession() {
  const runtimePath = globalThis.__RUNTIME_CONFIG__?.DEERFLOW_SSO_URL;
  const ssoUrl =
    typeof runtimePath === "string" && runtimePath.startsWith("/")
      ? runtimePath
      : SSO_PATH;
  const response = await fetch(ssoUrl, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const data = await response.json().catch(() => null);
  const tenant = data && (data.active_tenant || data.tenant);

  if (
    !response.ok ||
    !data?.success ||
    typeof data.token !== "string" ||
    !data.token ||
    !data.user ||
    !tenant
  ) {
    throw new Error(`SSO bootstrap rejected with HTTP ${response.status}`);
  }

  let previousUser = null;
  try {
    previousUser = JSON.parse(localStorage.getItem("weknora_user") || "null");
  } catch (_) {}
  if (
    previousUser?.id &&
    String(previousUser.id) !== String(data.user.id)
  ) {
    clearTenantScopedState();
  }

  // A failed standalone login may persist Lite mode. DeerFlow always embeds the
  // full workspace; leaving Lite state behind hides Shared Spaces and Agents.
  localStorage.removeItem("weknora_lite_mode");
  sessionStorage.removeItem("weknora_lite_last_path");
  localStorage.removeItem("weknora_auto_setup_failed");
  localStorage.setItem("weknora_token", data.token);
  if (data.refresh_token) {
    localStorage.setItem("weknora_refresh_token", data.refresh_token);
  } else {
    localStorage.removeItem("weknora_refresh_token");
  }
  localStorage.setItem("weknora_user", JSON.stringify(data.user));
  localStorage.setItem("weknora_tenant", JSON.stringify(tenant));
  localStorage.setItem(
    "weknora_memberships",
    JSON.stringify(data.memberships || []),
  );

  const policy = data.ui_policy || {
    mode: data.user?.is_system_admin
      ? "system_admin"
      : "personal_workspace_owner",
    full_ui: Boolean(data.user?.is_system_admin),
    default_path: DEFAULT_PATH,
  };
  localStorage.setItem(POLICY_KEY, JSON.stringify(policy));
  document.documentElement.setAttribute(
    "data-deerflow-weknora-ui",
    policy.mode || "personal_workspace_owner",
  );
  if (
    policy.full_ui === false &&
    !localStorage.getItem("weknora_last_chat_model_id")
  ) {
    localStorage.setItem("weknora_last_chat_model_id", DEFAULT_CHAT_MODEL_ID);
  }
  await validateRememberedTenant(data.token, tenant);
}

function isStandardUser() {
  return (
    document.documentElement.getAttribute("data-deerflow-weknora-ui") ===
    "personal_workspace_owner"
  );
}

const HIDDEN_STANDARD_LABELS = [
  "发布集成",
  "Publish Integrations",
  "Integrations",
];
const HIDDEN_USER_LINKS = ["Claw Skill", "Chrome 插件", "GitHub"];
const KB_SETTINGS_LABELS = [
  "知识库设置",
  "Knowledge Base Settings",
  "Knowledge Base Configuration",
];
const SHARING_LABELS = ["共享管理", "Sharing Management", "Share Management"];
const ALLOWED_SIDEBAR_GUIDES = [
  "nav-creatChat",
  "nav-knowledge-bases",
  "nav-organizations",
  "nav-agents",
];
let sidebarExpansionApplied = false;

function normalizedText(node) {
  return (node?.textContent || "").replace(/\s+/g, " ").trim();
}

function matchesLabel(node, labels) {
  const text = normalizedText(node);
  return labels.some((label) => text === label || text.includes(label));
}

function expandSidebarOnce() {
  if (sidebarExpansionApplied) return;
  localStorage.setItem("sidebar_collapsed", "false");
  const sidebar = document.querySelector(".aside_box");
  if (!sidebar) return;
  if (sidebar.classList.contains("aside_box--collapsed")) {
    const button = sidebar.querySelector(".sidebar-toggle");
    if (!(button instanceof HTMLElement)) return;
    button.click();
  }
  sidebarExpansionApplied = true;
}

function hideStandardSidebarEntries() {
  if (!isStandardUser()) return;
  document
    .querySelectorAll('.aside_box [data-guide^="nav-"]')
    .forEach((item) => {
      const guide = item.getAttribute("data-guide") || "";
      if (ALLOWED_SIDEBAR_GUIDES.includes(guide)) return;
      const box = item.closest(".menu_box") || item;
      item.setAttribute("data-deerflow-standard-sidebar-hidden", "true");
      box.setAttribute("data-deerflow-standard-sidebar-hidden", "true");
      item.hidden = true;
      box.hidden = true;
    });
}

function hideStandardSettingsGroup() {
  if (!isStandardUser()) return;
  let hideUntilNextGroup = false;
  document
    .querySelectorAll(".settings-nav .nav-group-title, .settings-nav .nav-item")
    .forEach((item) => {
      if (item.classList.contains("nav-group-title")) {
        hideUntilNextGroup = matchesLabel(item, HIDDEN_STANDARD_LABELS);
      }
      if (hideUntilNextGroup) item.hidden = true;
    });
}

function simplifyKnowledgeBaseEditor() {
  if (!isStandardUser()) return;
  document.querySelectorAll(".settings-modal").forEach((modal) => {
    const sidebar = modal.querySelector('[data-guide="kb-editor-sidebar"]');
    if (!sidebar) return;
    const existingSettings = KB_SETTINGS_LABELS.some((label) =>
      normalizedText(modal).includes(label),
    );
    const firstPass = modal.getAttribute("data-deerflow-kb-editor") !== "simple";
    modal.setAttribute("data-deerflow-kb-editor", "simple");

    const basic = sidebar.querySelector('[data-guide="kb-editor-nav-basic"]');
    if (
      firstPass &&
      basic instanceof HTMLElement &&
      !basic.classList.contains("active")
    ) {
      basic.click();
    }

    sidebar
      .querySelectorAll('.nav-item:not([data-guide="kb-editor-nav-basic"])')
      .forEach((item) => {
        const sharing = existingSettings && matchesLabel(item, SHARING_LABELS);
        if (sharing) {
          item.setAttribute("data-deerflow-kb-sharing-item", "true");
          item.hidden = false;
        } else {
          item.removeAttribute("data-deerflow-kb-sharing-item");
          item.hidden = true;
        }
      });
    sidebar.querySelectorAll(".nav-group-title").forEach((item) => {
      const sharingGroup =
        existingSettings && matchesLabel(item, HIDDEN_STANDARD_LABELS);
      if (sharingGroup) {
        item.setAttribute("data-deerflow-kb-sharing-group", "true");
        item.hidden = false;
      } else {
        item.removeAttribute("data-deerflow-kb-sharing-group");
        item.hidden = true;
      }
    });
  });
}

function simplifyPresentation() {
  expandSidebarOnce();
  if (!isStandardUser()) return;
  hideStandardSidebarEntries();
  document
    .querySelectorAll(".aside_box .menu_box, .user-dropdown .menu-item")
    .forEach((item) => {
      if (matchesLabel(item, HIDDEN_STANDARD_LABELS)) item.hidden = true;
    });
  hideStandardSettingsGroup();
  simplifyKnowledgeBaseEditor();
}

function replaceBrand(value) {
  return typeof value === "string" ? value.replace(/WeKnora/gi, "知识库") : value;
}

function brandNode(node) {
  if (node.nodeType === Node.TEXT_NODE) {
    const parent = node.parentElement;
    if (parent && !parent.closest("script,style,code,pre")) {
      // MutationObserver reports characterData writes even when the assigned
      // value is identical.  Writing the already-normalized value back here
      // therefore creates an unbounded microtask loop and prevents Vue from
      // painting.  Only mutate when the replacement actually changed text.
      const currentValue = node.nodeValue;
      const nextValue = replaceBrand(currentValue);
      if (nextValue !== currentValue) node.nodeValue = nextValue;
      const menuItem = parent.closest(".user-dropdown .menu-item");
      if (menuItem && matchesLabel(menuItem, HIDDEN_USER_LINKS)) menuItem.remove();
    }
    return;
  }
  if (!(node instanceof Element)) return;
  const menuItem = node.closest(".user-dropdown .menu-item");
  if (menuItem && matchesLabel(menuItem, HIDDEN_USER_LINKS)) {
    menuItem.remove();
    return;
  }
  for (const attribute of ["aria-label", "alt", "placeholder", "title"]) {
    if (node.hasAttribute(attribute)) {
      const currentValue = node.getAttribute(attribute);
      const nextValue = replaceBrand(currentValue);
      if (nextValue !== currentValue) node.setAttribute(attribute, nextValue);
    }
  }
  Array.from(node.childNodes).forEach(brandNode);
}

function updateDocumentTitle() {
  const nextTitle = replaceBrand(document.title) || "知识库";
  if (nextTitle !== document.title) document.title = nextTitle;
}

function installPresentationLayer() {
  const start = () => {
    updateDocumentTitle();
    if (document.body) brandNode(document.body);
    simplifyPresentation();
    new MutationObserver((records) => {
      for (const record of records) {
        record.addedNodes.forEach(brandNode);
        if (record.type === "characterData") brandNode(record.target);
      }
      updateDocumentTitle();
      simplifyPresentation();
    }).observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}

let startupFinished = false;
let startupFailureRendered = false;

function renderBootstrapFailure(error) {
  if (startupFinished || startupFailureRendered) return;
  startupFailureRendered = true;
  console.error("[deerflow-weknora-sso] bootstrap failed", error);
  const root = document.getElementById("app");
  if (!root) return;
  root.innerHTML = `
    <main class="deerflow-weknora-bootstrap-error" role="alert">
      <section class="deerflow-weknora-bootstrap-error__card">
        <h1>知识库登录未完成</h1>
        <p>当前会话未能安全进入知识库，请刷新后重试。</p>
        <button type="button" id="deerflow-weknora-retry">重新登录</button>
      </section>
    </main>`;
  document
    .getElementById("deerflow-weknora-retry")
    ?.addEventListener("click", () => location.reload());
}

function handleStartupError(event) {
  if (startupFinished || startupFailureRendered) return;
  const error = event?.reason || event?.error;
  // Resource load failures are reported as ErrorEvent objects without an
  // Error instance.  They are non-fatal here and remain visible in DevTools.
  if (!error) return;
  event.preventDefault?.();
  renderBootstrapFailure(error);
}

async function waitForApplicationMount(timeoutMilliseconds = 30000) {
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    const root = document.getElementById("app");
    if (root?.hasAttribute("data-v-app")) return;
    if (startupFailureRendered) throw new Error("WeKnora startup failed");
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("WeKnora application mount timed out");
}

window.addEventListener("error", handleStartupError);
window.addEventListener("unhandledrejection", handleStartupError);

try {
  await bootstrapSession();
  installNavigationGuard();
  installPresentationLayer();
  const applicationModule = globalThis.__WEKNORA_MAIN_MODULE__;
  if (!/^\/assets\/[A-Za-z0-9_.-]+\.js$/.test(applicationModule || "")) {
    throw new Error("invalid WeKnora v0.8 application module");
  }
  await import(applicationModule);
  // The upstream entry starts an async bootstrap without exporting its
  // promise.  Import success alone does not prove Vue mounted.  Hold the
  // enterprise gate until the real root is attached, and surface a bounded
  // error instead of leaving a silent white page.
  await waitForApplicationMount();
  startupFinished = true;
} catch (error) {
  renderBootstrapFailure(error);
} finally {
  window.removeEventListener("error", handleStartupError);
  window.removeEventListener("unhandledrejection", handleStartupError);
}
