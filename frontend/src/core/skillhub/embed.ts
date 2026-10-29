export const SKILLHUB_BASE_PATH = "/platform/skillhub";

export const SKILLHUB_ROUTES = {
  discover: `${SKILLHUB_BASE_PATH}/search?q=&sort=relevance&page=0&starredOnly=false`,
  dashboard: `${SKILLHUB_BASE_PATH}/dashboard`,
  stars: `${SKILLHUB_BASE_PATH}/dashboard/stars`,
  subscriptions: `${SKILLHUB_BASE_PATH}/dashboard/subscriptions`,
  mySkills: `${SKILLHUB_BASE_PATH}/dashboard/skills`,
  myNamespaces: `${SKILLHUB_BASE_PATH}/dashboard/namespaces`,
  publish: `${SKILLHUB_BASE_PATH}/dashboard/publish`,
  governance: `${SKILLHUB_BASE_PATH}/dashboard/governance`,
  reviews: `${SKILLHUB_BASE_PATH}/dashboard/reviews`,
  reviewProgress: `${SKILLHUB_BASE_PATH}/dashboard/review-progress`,
  promotions: `${SKILLHUB_BASE_PATH}/dashboard/promotions`,
  reports: `${SKILLHUB_BASE_PATH}/dashboard/reports`,
  notifications: `${SKILLHUB_BASE_PATH}/dashboard/notifications`,
  tokens: `${SKILLHUB_BASE_PATH}/dashboard/tokens`,
  profile: `${SKILLHUB_BASE_PATH}/settings/profile`,
  notificationSettings: `${SKILLHUB_BASE_PATH}/settings/notifications`,
  security: `${SKILLHUB_BASE_PATH}/settings/security`,
  users: `${SKILLHUB_BASE_PATH}/admin/users`,
  labels: `${SKILLHUB_BASE_PATH}/admin/labels`,
  adminNamespaces: `${SKILLHUB_BASE_PATH}/admin/namespaces`,
  auditLog: `${SKILLHUB_BASE_PATH}/admin/audit-log`,
} as const;

/**
 * Discovery is the useful first screen for both ordinary users and operators.
 * Account metadata and API tokens remain available inside SkillHub but are not
 * promoted as the DeerFlow Skill Center's landing experience.
 */
export const SKILLHUB_EMBED_PATH = SKILLHUB_ROUTES.discover;
export const SKILLHUB_AUTH_ME_PATH = `${SKILLHUB_BASE_PATH}/api/v1/auth/me`;
export const SKILLHUB_MY_NAMESPACES_PATH = `${SKILLHUB_BASE_PATH}/api/web/me/namespaces`;
export const SKILLHUB_SESSION_BOOTSTRAP_PATH = `${SKILLHUB_BASE_PATH}/api/v1/auth/session/bootstrap`;
export const SKILLHUB_SESSION_PROVIDER = "deerflow-session";

export type SkillHubUser = {
  userId: string;
  displayName: string;
  platformRoles: string[];
  canChangePassword: boolean;
};

export type SkillHubManagedNamespace = {
  type: string;
  status: string;
  currentUserRole?: string;
};

type SkillHubAuthEnvelope = {
  code?: number;
  msg?: string;
  data?: Partial<SkillHubUser> | null;
};

export class SkillHubSessionBootstrapError extends Error {
  constructor(
    message: string,
    public readonly status: number | null = null,
  ) {
    super(message);
    this.name = "SkillHubSessionBootstrapError";
  }
}

export function readSkillHubUser(payload: unknown): SkillHubUser | null {
  const data = (payload as SkillHubAuthEnvelope | null)?.data;
  if (!data || typeof data.userId !== "string") {
    return null;
  }

  return {
    userId: data.userId,
    displayName:
      typeof data.displayName === "string" && data.displayName.trim()
        ? data.displayName
        : data.userId,
    platformRoles: Array.isArray(data.platformRoles)
      ? data.platformRoles.filter(
          (role): role is string => typeof role === "string",
        )
      : [],
    canChangePassword: data.canChangePassword === true,
  };
}

export function hasSkillHubRole(user: SkillHubUser | null, ...roles: string[]) {
  return roles.some((role) => user?.platformRoles.includes(role));
}

export function readSkillHubManagedNamespaces(
  payload: unknown,
): SkillHubManagedNamespace[] {
  const data = (payload as { data?: unknown } | null)?.data;
  if (!Array.isArray(data)) return [];

  return data.flatMap((entry): SkillHubManagedNamespace[] => {
    if (!entry || typeof entry !== "object") return [];
    const namespace = entry as Record<string, unknown>;
    if (
      typeof namespace.type !== "string" ||
      typeof namespace.status !== "string"
    ) {
      return [];
    }
    return [
      {
        type: namespace.type,
        status: namespace.status,
        currentUserRole:
          typeof namespace.currentUserRole === "string"
            ? namespace.currentUserRole
            : undefined,
      },
    ];
  });
}

/** Mirrors native SkillHub review-center visibility for team namespace owners. */
export function hasSkillHubNamespaceReviewAccess(
  namespaces: readonly SkillHubManagedNamespace[],
) {
  return namespaces.some(
    (namespace) =>
      namespace.type === "TEAM" &&
      (namespace.currentUserRole === "OWNER" ||
        namespace.currentUserRole === "ADMIN"),
  );
}

export async function loadSkillHubManagedNamespaces(
  signal?: AbortSignal,
): Promise<SkillHubManagedNamespace[]> {
  const response = await fetch(SKILLHUB_MY_NAMESPACES_PATH, {
    headers: { Accept: "application/json" },
    credentials: "include",
    cache: "no-store",
    signal,
  });
  if (!response.ok) return [];
  return readSkillHubManagedNamespaces(await response.json().catch(() => null));
}

function skillHubBootstrapErrorMessage(status: number, payload: unknown) {
  if (status === 401) {
    return "当前登录状态已失效，请重新登录后再试。";
  }
  if (status === 403) {
    return "当前账号暂时无法进入技能中心，请联系管理员。";
  }
  if (status === 429) {
    return "技能中心登录请求过于频繁，请稍后重试。";
  }
  if (status >= 500) {
    return "技能中心认证服务暂时不可用，请稍后重试。";
  }

  const message = (payload as SkillHubAuthEnvelope | null)?.msg;
  return typeof message === "string" && message.trim()
    ? message
    : "无法进入技能中心，请稍后重试。";
}

/**
 * Establish the native SkillHub session from the current same-origin platform
 * session before any SkillHub page or protected API is loaded. The native
 * bridge creates a first-time account and reconciles profile fields on every
 * subsequent entry; platform roles continue to come only from SkillHub.
 */
export async function bootstrapSkillHubSession(
  signal?: AbortSignal,
): Promise<SkillHubUser> {
  const response = await fetch(SKILLHUB_SESSION_BOOTSTRAP_PATH, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ provider: SKILLHUB_SESSION_PROVIDER }),
    credentials: "include",
    cache: "no-store",
    signal,
  });
  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    throw new SkillHubSessionBootstrapError(
      skillHubBootstrapErrorMessage(response.status, payload),
      response.status,
    );
  }

  const user = readSkillHubUser(payload);
  if (!user) {
    throw new SkillHubSessionBootstrapError(
      "技能中心返回了无效的账号信息，请稍后重试。",
      response.status,
    );
  }
  return user;
}

const SKILLHUB_EMBED_STYLE_ID = "deerflow-skillhub-embedded-theme";

export function sanitizeSkillHubVisibleText(value: string) {
  return value
    .replace(/admin@deerflow\.local/gi, "admin@local.test")
    .replace(/deerflow-local-admin/gi, "local-platform-admin")
    .replace(/deerflow local admin/gi, "本地系统管理员")
    .replace(/deerflow/gi, "本平台");
}

const SKILLHUB_EMBED_CSS = `
  :root {
    --background: 216 33% 97% !important;
    --foreground: 211 41% 21% !important;
    --card: 0 0% 100% !important;
    --card-foreground: 211 38% 24% !important;
    --popover: 0 0% 100% !important;
    --popover-foreground: 211 38% 24% !important;
    --primary: 205 100% 35% !important;
    --primary-foreground: 0 0% 100% !important;
    --secondary: 211 64% 95% !important;
    --secondary-foreground: 211 62% 31% !important;
    --muted: 210 40% 95% !important;
    --muted-foreground: 211 19% 40% !important;
    --accent: 211 65% 92% !important;
    --accent-foreground: 211 71% 34% !important;
    --border: 212 32% 90% !important;
    --input: 211 28% 79% !important;
    --ring: 210 79% 46% !important;
    --radius: 0.75rem !important;
    --surface-glass: 210 50% 98% !important;
    --glow-primary: 210 100% 37% !important;
    --glow-accent: 204 73% 48% !important;
    --brand-start: #0067b1 !important;
    --brand-end: #0067b1 !important;
    --brand-gradient: linear-gradient(135deg, #0067b1 0%, #0067b1 100%) !important;
    --text-secondary: 211 28% 36% !important;
    --text-muted: 211 19% 45% !important;
    --text-placeholder: 211 14% 63% !important;
    --border-card: 210 34% 89% !important;
    --bg-page: #f5f7fa !important;
  }

  html,
  body,
  #root {
    min-height: 100%;
    background: #f5f7fa !important;
  }

  body,
  h1,
  h2,
  h3,
  h4,
  h5,
  h6,
  button,
  input,
  select,
  textarea {
    font-family: Inter, "Microsoft YaHei", "PingFang SC", system-ui, sans-serif !important;
  }

  body > #root > div > header,
  body > #root > div > footer,
  body > #root > div > div[aria-hidden="true"] {
    display: none !important;
  }

  body > #root > div {
    min-height: 100vh !important;
    background: #f5f7fa !important;
  }

  body > #root > div > main {
    padding: 24px !important;
  }

  body > #root > div > main > .mx-auto.w-full {
    max-width: none !important;
  }

  body > #root > div > main h1 {
    font-size: 24px !important;
    line-height: 1.4 !important;
    font-weight: 650 !important;
  }

  [data-deerflow-shell-only="true"] {
    display: none !important;
  }

  /*
   * SkillHub uses semantic shadcn variables for most controls, but several
   * badges and decorative accents ship with fixed Tailwind colors. Normalize
   * those non-destructive accents here so embedded pages read as one product.
   * Destructive/error controls intentionally retain their red warning color.
   */
  [class*="bg-indigo-"],
  [class*="bg-violet-"],
  [class*="bg-purple-"] {
    background-color: rgba(0, 103, 177, 0.1) !important;
  }

  [class*="text-indigo-"],
  [class*="text-violet-"],
  [class*="text-purple-"] {
    color: #0067b1 !important;
  }

  [class*="border-indigo-"],
  [class*="border-violet-"],
  [class*="border-purple-"] {
    border-color: rgba(0, 103, 177, 0.24) !important;
  }

  [class*="from-indigo-"],
  [class*="from-violet-"],
  [class*="from-purple-"] {
    --tw-gradient-from: #0067b1 var(--tw-gradient-from-position) !important;
    --tw-gradient-to: rgba(0, 103, 177, 0) var(--tw-gradient-to-position) !important;
  }

  [class*="via-indigo-"],
  [class*="via-violet-"],
  [class*="via-purple-"] {
    --tw-gradient-via: #0067b1 var(--tw-gradient-via-position) !important;
  }

  [class*="to-indigo-"],
  [class*="to-violet-"],
  [class*="to-purple-"] {
    --tw-gradient-to: #0067b1 var(--tw-gradient-to-position) !important;
  }

  .badge-soft-blue,
  .badge-soft-purple {
    border-color: rgba(0, 103, 177, 0.22) !important;
    background: rgba(0, 103, 177, 0.09) !important;
    color: #0067b1 !important;
  }

  button[class*="border-border"][class*="bg-transparent"],
  button[class*="border-border"][class*="bg-background"],
  [role="button"][class*="border-border"][class*="bg-transparent"] {
    border-color: rgba(0, 103, 177, 0.22) !important;
    color: #0067b1 !important;
  }

  [data-state="active"],
  [aria-selected="true"],
  [aria-current="page"] {
    --tw-ring-color: rgba(0, 103, 177, 0.32) !important;
  }

  input:focus,
  input:focus-visible,
  select:focus,
  select:focus-visible,
  textarea:focus,
  textarea:focus-visible {
    border-color: rgba(0, 103, 177, 0.62) !important;
    box-shadow: 0 0 0 3px rgba(0, 103, 177, 0.12) !important;
  }

  .text-brand-gradient {
    background-image: var(--brand-gradient) !important;
  }

  /* Accent is a surface token; namespace labels need its readable foreground. */
  .text-accent { color: #005592 !important; }
  button:not([role="switch"]):not([role="checkbox"]),
  [role="combobox"] {
    min-height: 36px;
    border-radius: 10px !important;
    font-size: 14px;
    font-weight: 500;
    box-shadow: none !important;
  }
  button[class*="bg-primary"] {
    background: #0067b1 !important;
    color: #ffffff !important;
  }
  button[class*="bg-primary"]:hover:not(:disabled) { background: #005592 !important; }
  button:focus-visible, a:focus-visible {
    outline: 2px solid #0067b1; outline-offset: 2px;
  }
  a[class*="rounded-2xl"] { border-radius: 12px !important; box-shadow: none !important; }

  /*
   * Native SkillHub intentionally keeps Radix Select inside its Dialog so the
   * menu inherits the dialog stacking context. Let that dialog expose the
   * open menu and make the viewport itself scrollable on short screens.
   */
  [role="dialog"][data-deerflow-open-select="true"] {
    overflow: visible !important;
  }

  [role="listbox"],
  [data-radix-select-viewport] {
    height: auto !important;
    max-height: min(18rem, calc(100dvh - 6rem)) !important;
    overflow-y: auto !important;
    overscroll-behavior: contain;
    -webkit-overflow-scrolling: touch;
    touch-action: pan-y;
  }

  @supports not (height: 100dvh) {
    [role="listbox"],
    [data-radix-select-viewport] {
      max-height: min(18rem, calc(100vh - 6rem)) !important;
    }
  }

  ::selection {
    background: rgba(0, 103, 177, 0.2);
  }

  @media (max-width: 767px) {
    html, body, #root { min-width: 0 !important; }
    body > #root > div > main {
      min-width: 0; padding: 16px 16px 24px !important;
    }
    body > #root > div > main h1 { font-size: 22px !important; }
    input, select, textarea { font-size: 16px !important; }
    button:not([role="switch"]):not([role="checkbox"]),
    [role="combobox"], [role="tab"], [role="menuitem"] { min-height: 44px; }
    [role="dialog"] {
      width: calc(100vw - 24px) !important;
      max-width: calc(100vw - 24px) !important;
      max-height: calc(100vh - 24px);
      max-height: calc(100dvh - 24px);
      overflow-y: auto;
      overscroll-behavior: contain;
    }
    [role="tablist"] { max-width: 100%; overflow-x: auto; justify-content: flex-start; }
    pre { max-width: 100%; overflow-x: auto; }
    h1, h2, h3 { overflow-wrap: anywhere; }
  }
`;

/**
 * SkillHub is served from the same browser origin, so DeerFlow can apply a
 * presentation-only embedded theme without forking registry behavior. Native
 * SkillHub routes and API authorization remain the security boundary.
 */
export function applySkillHubEmbeddedTheme(frame: HTMLIFrameElement) {
  try {
    const frameDocument = frame.contentDocument;
    if (!frameDocument?.head) {
      return false;
    }

    frameDocument.documentElement.dataset.deerflowEmbedded = "true";
    let style = frameDocument.getElementById(SKILLHUB_EMBED_STYLE_ID);
    if (!style) {
      style = frameDocument.createElement("style");
      style.id = SKILLHUB_EMBED_STYLE_ID;
      frameDocument.head.appendChild(style);
    }
    style.textContent = SKILLHUB_EMBED_CSS;

    const applyEmbeddedPresentation = () => {
      frameDocument.querySelectorAll("button").forEach((button) => {
        const label = button.textContent?.trim();
        if (
          label === "返回控制台" ||
          label === "Back to Dashboard" ||
          label === "Назад к панели"
        ) {
          button.dataset.deerflowShellOnly = "true";
        }
        if (
          label === "下载技能 ZIP" ||
          label === "Download Skill ZIP" ||
          label === "Скачать ZIP скилла"
        ) {
          button.dataset.deerflowShellOnly = "true";
        }
      });

      if (frameDocument.body) {
        const walker = frameDocument.createTreeWalker(
          frameDocument.body,
          4, // NodeFilter.SHOW_TEXT, kept realm-independent for the iframe.
        );
        let textNode = walker.nextNode();
        while (textNode) {
          const parentName = textNode.parentElement?.tagName;
          if (
            parentName !== "SCRIPT" &&
            parentName !== "STYLE" &&
            parentName !== "NOSCRIPT" &&
            textNode.nodeValue
          ) {
            const sanitized = sanitizeSkillHubVisibleText(textNode.nodeValue);
            if (sanitized !== textNode.nodeValue) {
              textNode.nodeValue = sanitized;
            }
          }
          textNode = walker.nextNode();
        }

        frameDocument
          .querySelectorAll<HTMLElement>("[aria-label], [title], [placeholder]")
          .forEach((element) => {
            for (const attribute of ["aria-label", "title", "placeholder"]) {
              const value = element.getAttribute(attribute);
              if (value) {
                element.setAttribute(
                  attribute,
                  sanitizeSkillHubVisibleText(value),
                );
              }
            }
          });

        frameDocument
          .querySelectorAll<HTMLElement>('[role="dialog"]')
          .forEach((dialog) => {
            if (
              dialog.querySelector(
                '[role="listbox"], [data-radix-select-viewport]',
              )
            ) {
              dialog.dataset.deerflowOpenSelect = "true";
            } else {
              delete dialog.dataset.deerflowOpenSelect;
            }
          });
      }
    };
    applyEmbeddedPresentation();

    const FrameMutationObserver = (
      frame.contentWindow as
        | (Window & { MutationObserver?: typeof MutationObserver })
        | null
    )?.MutationObserver;
    if (
      frameDocument.body &&
      FrameMutationObserver &&
      !frameDocument.documentElement.dataset.deerflowShellObserver
    ) {
      // Use the iframe realm's observer. A parent-window MutationObserver
      // rejects an iframe-owned body as a non-Node in some browsers.
      const observer = new FrameMutationObserver(applyEmbeddedPresentation);
      observer.observe(frameDocument.body, {
        childList: true,
        characterData: true,
        subtree: true,
      });
      frameDocument.documentElement.dataset.deerflowShellObserver = "true";
    }
    return true;
  } catch {
    return false;
  }
}
