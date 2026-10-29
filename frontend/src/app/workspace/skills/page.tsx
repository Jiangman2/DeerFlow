"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  BellIcon,
  BookOpenCheckIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ClipboardCheckIcon,
  ExternalLinkIcon,
  FileWarningIcon,
  FolderCogIcon,
  KeyRoundIcon,
  LayoutDashboardIcon,
  LibraryIcon,
  PackageSearchIcon,
  LoaderCircleIcon,
  PlusIcon,
  PowerIcon,
  RefreshCwIcon,
  ScrollTextIcon,
  ShieldCheckIcon,
  StarIcon,
  TagsIcon,
  Trash2Icon,
  UploadCloudIcon,
  UsersIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { Switch } from "@/components/ui/switch";
import { ModulePageTitle } from "@/components/workspace/module-page-title";
import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";
import {
  applySkillHubEmbeddedTheme,
  bootstrapSkillHubSession,
  buildUserSkillLibrary,
  hasSkillHubNamespaceReviewAccess,
  hasSkillHubRole,
  installSkillHubRuntimeSkill,
  loadSkillHubDetail,
  loadSkillHubManagedNamespaces,
  loadSkillHubRuntimeSkills,
  readSkillHubSkillLocation,
  readSkillHubUser,
  setSkillHubRuntimeEnabled,
  SkillHubSessionBootstrapError,
  skillHubDetailPath,
  SKILLHUB_AUTH_ME_PATH,
  SKILLHUB_EMBED_PATH,
  SKILLHUB_ROUTES,
  type SkillHubDetail,
  type SkillHubRuntimeSkill,
  type SkillHubUser,
  uninstallSkillHubRuntimeSkill,
} from "@/core/skillhub";
import { useEnableSkill, useSkills } from "@/core/skills/hooks";
import type { Skill } from "@/core/skills/type";
import { env } from "@/env";
import { cn } from "@/lib/utils";

type SkillCenterNavItem = {
  label: string;
  path: string;
  icon: LucideIcon;
};

function SkillCenterNavButton({
  label,
  icon: Icon,
  active,
  onClick,
}: {
  label: string;
  icon: LucideIcon;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-current={active ? "page" : undefined}
      onClick={onClick}
      className={cn(
        "cnooc-skill-nav-item focus-visible:ring-ring/50 flex h-11 shrink-0 items-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none md:h-9",
        active
          ? "bg-primary text-primary-foreground shadow-primary/15 shadow-sm"
          : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
      )}
    >
      <Icon className="size-4" />
      {label}
    </button>
  );
}

const PERSONAL_NAV_ITEMS: SkillCenterNavItem[] = [
  {
    label: "技能广场",
    path: SKILLHUB_ROUTES.discover,
    icon: PackageSearchIcon,
  },
  {
    label: "我的收藏",
    path: SKILLHUB_ROUTES.stars,
    icon: StarIcon,
  },
  {
    label: "我的订阅",
    path: SKILLHUB_ROUTES.subscriptions,
    icon: BellIcon,
  },
];

const WORKSPACE_PATHS = new Set<string>([
  SKILLHUB_ROUTES.dashboard,
  SKILLHUB_ROUTES.mySkills,
  SKILLHUB_ROUTES.myNamespaces,
  SKILLHUB_ROUTES.publish,
  SKILLHUB_ROUTES.governance,
  SKILLHUB_ROUTES.reviews,
  SKILLHUB_ROUTES.reviewProgress,
  SKILLHUB_ROUTES.promotions,
  SKILLHUB_ROUTES.reports,
  SKILLHUB_ROUTES.notifications,
  SKILLHUB_ROUTES.tokens,
  SKILLHUB_ROUTES.profile,
  SKILLHUB_ROUTES.notificationSettings,
  SKILLHUB_ROUTES.security,
  SKILLHUB_ROUTES.users,
  SKILLHUB_ROUTES.labels,
  SKILLHUB_ROUTES.adminNamespaces,
  SKILLHUB_ROUTES.auditLog,
]);

type SkillHubAccess = {
  skillAdmin: boolean;
  userAdmin: boolean;
  auditor: boolean;
  superAdmin: boolean;
  governance: boolean;
};

type SkillHubSessionState = "idle" | "bootstrapping" | "ready" | "error";

type SkillHubUserProbe = "authenticated" | "unauthorized" | "unavailable";

function SkillHubWorkspaceMenu({
  access,
  user,
  hasNamespaceReviewAccess,
  active,
  compact = false,
  onNavigate,
}: {
  access: SkillHubAccess;
  user: SkillHubUser | null;
  hasNamespaceReviewAccess: boolean;
  active: boolean;
  compact?: boolean;
  onNavigate: (path: string) => void;
}) {
  const item = ({ label, path, icon: Icon }: SkillCenterNavItem) => (
    <DropdownMenuItem key={path} onSelect={() => onNavigate(path)}>
      <Icon />
      {label}
    </DropdownMenuItem>
  );

  const managementView =
    access.skillAdmin ||
    access.userAdmin ||
    access.auditor ||
    access.governance ||
    hasNamespaceReviewAccess;
  const reviewCenterVisible =
    access.skillAdmin || access.userAdmin || hasNamespaceReviewAccess;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={managementView ? "管理中心" : "技能工作台"}
          className={cn(
            "focus-visible:ring-ring/50 flex h-11 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none md:h-9",
            active
              ? "bg-primary text-primary-foreground shadow-primary/15 shadow-sm"
              : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
          )}
        >
          {managementView ? (
            <ShieldCheckIcon className="size-4" />
          ) : (
            <LayoutDashboardIcon className="size-4" />
          )}
          <span className={compact ? "hidden min-[380px]:inline" : undefined}>
            {managementView ? "管理中心" : "工作台"}
          </span>
          <ChevronDownIcon className="size-3.5" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align={compact ? "end" : "start"}
        className="cnooc-skill-management-menu max-h-[calc(100dvh-5rem)] w-56 overflow-y-auto overscroll-contain"
      >
        <DropdownMenuLabel>个人工作台</DropdownMenuLabel>
        {item({
          label: "个人概览",
          path: SKILLHUB_ROUTES.dashboard,
          icon: LayoutDashboardIcon,
        })}
        {item({
          label: "已发布技能",
          path: SKILLHUB_ROUTES.mySkills,
          icon: BookOpenCheckIcon,
        })}
        {item({
          label: "我的命名空间",
          path: SKILLHUB_ROUTES.myNamespaces,
          icon: LibraryIcon,
        })}
        {item({
          label: "审核进度",
          path: SKILLHUB_ROUTES.reviewProgress,
          icon: CheckCircle2Icon,
        })}
        {item({
          label: "消息通知",
          path: SKILLHUB_ROUTES.notifications,
          icon: BellIcon,
        })}
        {item({
          label: "访问令牌",
          path: SKILLHUB_ROUTES.tokens,
          icon: KeyRoundIcon,
        })}
        {item({
          label: "个人资料",
          path: SKILLHUB_ROUTES.profile,
          icon: UsersIcon,
        })}
        {item({
          label: "通知设置",
          path: SKILLHUB_ROUTES.notificationSettings,
          icon: BellIcon,
        })}
        {user?.canChangePassword
          ? item({
              label: "安全设置",
              path: SKILLHUB_ROUTES.security,
              icon: ShieldCheckIcon,
            })
          : null}

        {managementView || access.governance ? <DropdownMenuSeparator /> : null}
        {managementView || access.governance ? (
          <DropdownMenuLabel>技能运营</DropdownMenuLabel>
        ) : null}
        {access.skillAdmin
          ? item({
              label: "发布技能",
              path: SKILLHUB_ROUTES.publish,
              icon: UploadCloudIcon,
            })
          : null}
        {reviewCenterVisible
          ? item({
              label: "审核中心",
              path: SKILLHUB_ROUTES.reviews,
              icon: ClipboardCheckIcon,
            })
          : null}
        {access.governance
          ? item({
              label: "审核与治理",
              path: SKILLHUB_ROUTES.governance,
              icon: ClipboardCheckIcon,
            })
          : null}
        {access.skillAdmin
          ? item({
              label: "提升申请",
              path: SKILLHUB_ROUTES.promotions,
              icon: BookOpenCheckIcon,
            })
          : null}
        {access.skillAdmin
          ? item({
              label: "举报处理",
              path: SKILLHUB_ROUTES.reports,
              icon: FileWarningIcon,
            })
          : null}

        {access.userAdmin || access.auditor ? <DropdownMenuSeparator /> : null}
        {access.userAdmin
          ? item({
              label: "用户管理",
              path: SKILLHUB_ROUTES.users,
              icon: UsersIcon,
            })
          : null}
        {access.auditor
          ? item({
              label: "审计日志",
              path: SKILLHUB_ROUTES.auditLog,
              icon: ScrollTextIcon,
            })
          : null}

        {access.superAdmin ? <DropdownMenuSeparator /> : null}
        {access.superAdmin
          ? item({
              label: "标签管理",
              path: SKILLHUB_ROUTES.labels,
              icon: TagsIcon,
            })
          : null}
        {access.superAdmin
          ? item({
              label: "命名空间治理",
              path: SKILLHUB_ROUTES.adminNamespaces,
              icon: FolderCogIcon,
            })
          : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

type MySkillsPanelProps = {
  skills: Skill[];
  installations: SkillHubRuntimeSkill[];
  loading: boolean;
  errorMessage: string | null;
  isPlatformAdmin: boolean;
  mutating: boolean;
  onCreate: () => void;
  onOpenDetail: (installation: SkillHubRuntimeSkill) => void;
  onToggleCustom: (skill: Skill, enabled: boolean) => void;
  onToggleInstalled: (
    installation: SkillHubRuntimeSkill,
    enabled: boolean,
  ) => void;
  onUninstall: (
    installation: SkillHubRuntimeSkill,
    displayName: string,
  ) => void;
};

function MySkillsPanel({
  skills,
  installations,
  loading,
  errorMessage,
  isPlatformAdmin,
  mutating,
  onCreate,
  onOpenDetail,
  onToggleCustom,
  onToggleInstalled,
  onUninstall,
}: MySkillsPanelProps) {
  const items = useMemo(
    () => buildUserSkillLibrary(skills, installations),
    [installations, skills],
  );
  const enabledCount = items.filter((item) => item.enabled).length;
  const staticWebsite = env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true";

  return (
    <section className="cnooc-my-skills app-scroll-region h-full overflow-y-auto px-3 py-5 md:px-6 md:py-7">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-foreground text-xl font-semibold">我的技能</h2>
            <p className="text-muted-foreground mt-1 text-sm">
              管理当前账号个人目录中的技能；启用后可供新对话和允许加载该技能的岗位专家使用。
            </p>
          </div>
          <Button type="button" size="sm" onClick={onCreate}>
            <PlusIcon />
            新建技能
          </Button>
        </div>

        {!loading && !errorMessage ? (
          <div className="text-muted-foreground flex items-center gap-2 text-xs">
            <span>共 {items.length} 个技能</span>
            <span aria-hidden="true">·</span>
            <span>{enabledCount} 个已启用</span>
          </div>
        ) : null}

        {loading ? (
          <div className="border-border bg-card text-muted-foreground flex min-h-40 items-center justify-center gap-2 rounded-xl border text-sm">
            <LoaderCircleIcon className="size-4 animate-spin" />
            正在读取我的技能…
          </div>
        ) : errorMessage ? (
          <div className="border-destructive/30 bg-destructive/5 text-destructive rounded-xl border px-4 py-5 text-sm">
            {errorMessage}
          </div>
        ) : items.length === 0 ? (
          <div className="border-border bg-card flex min-h-56 flex-col items-center justify-center rounded-xl border px-6 text-center">
            <span className="bg-primary/10 text-primary flex size-11 items-center justify-center rounded-xl">
              <LibraryIcon className="size-5" />
            </span>
            <h3 className="text-foreground mt-3 text-sm font-semibold">
              还没有个人技能
            </h3>
            <p className="text-muted-foreground mt-1 max-w-md text-sm">
              可以从技能广场添加，也可以新建一个适合自己工作的技能。
            </p>
            <Button type="button" size="sm" className="mt-4" onClick={onCreate}>
              <PlusIcon />
              新建技能
            </Button>
          </div>
        ) : (
          <div className="border-border bg-card overflow-hidden rounded-xl border">
            {items.map((item) => {
              const installation = item.installation;
              const sourceLabel = installation
                ? `技能中心 · @${installation.namespace} · v${installation.version}`
                : "自建技能";
              const canToggleCustom = isPlatformAdmin && !staticWebsite;
              const toggleDisabled =
                mutating || (!installation && !canToggleCustom);
              return (
                <article
                  key={item.name}
                  className="cnooc-personal-skill border-border/80 flex flex-col gap-3 border-b p-4 last:border-b-0 sm:flex-row sm:items-center"
                >
                  <span className="bg-primary/10 text-primary flex size-10 shrink-0 items-center justify-center rounded-lg">
                    {installation ? (
                      <PackageSearchIcon className="size-4.5" />
                    ) : (
                      <LibraryIcon className="size-4.5" />
                    )}
                  </span>

                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <h3 className="text-foreground min-w-0 text-sm font-semibold wrap-anywhere sm:truncate">
                        {item.name}
                      </h3>
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 text-[11px] font-medium",
                          installation
                            ? "bg-primary/10 text-primary"
                            : "bg-muted text-muted-foreground",
                        )}
                      >
                        {sourceLabel}
                      </span>
                    </div>
                    <p className="text-muted-foreground mt-1 line-clamp-2 text-xs leading-5">
                      {item.description || "暂无技能说明"}
                    </p>
                  </div>

                  <div className="cnooc-personal-skill-actions flex shrink-0 flex-wrap items-center justify-between gap-2 sm:justify-end">
                    <label
                      className="text-muted-foreground flex h-9 items-center gap-2 text-xs font-medium"
                      title={
                        !installation && !canToggleCustom
                          ? "自建技能启停由平台管理员统一管理"
                          : undefined
                      }
                    >
                      {item.enabled ? "已启用" : "已停用"}
                      <Switch
                        checked={item.enabled}
                        disabled={toggleDisabled}
                        aria-label={`${item.enabled ? "停用" : "启用"}${item.name}`}
                        onCheckedChange={(enabled) =>
                          installation
                            ? onToggleInstalled(installation, enabled)
                            : onToggleCustom(
                                {
                                  name: item.name,
                                  description: item.description,
                                  category: "custom",
                                  license: "",
                                  enabled: item.enabled,
                                  editable: item.editable,
                                },
                                enabled,
                              )
                        }
                      />
                    </label>
                    {installation ? (
                      <>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => onOpenDetail(installation)}
                        >
                          <ExternalLinkIcon />
                          查看详情
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          disabled={mutating}
                          aria-label={`卸载${item.name}`}
                          title="卸载"
                          onClick={() => onUninstall(installation, item.name)}
                        >
                          <Trash2Icon />
                        </Button>
                      </>
                    ) : null}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}

type SkillRuntimeBarProps = {
  detail: SkillHubDetail;
  installation: SkillHubRuntimeSkill | null;
  loading: boolean;
  mutating: boolean;
  onInstall: () => void;
  onToggle: (enabled: boolean) => void;
  onUninstall: () => void;
};

function SkillRuntimeBar({
  detail,
  installation,
  loading,
  mutating,
  onInstall,
  onToggle,
  onUninstall,
}: SkillRuntimeBarProps) {
  const current =
    installation?.installed && installation.version === detail.version;
  const installLabel = !installation?.installed
    ? "添加到我的技能"
    : current
      ? "已安装"
      : `升级到 v${detail.version}`;

  return (
    <section className="cnooc-skill-runtime border-border/80 bg-card relative z-20 shrink-0 border-b px-3 py-2.5 md:px-5">
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-2.5">
        <div className="mr-auto max-w-full min-w-0 basis-full md:basis-auto">
          <div className="flex items-center gap-2">
            <h2 className="text-foreground truncate text-sm font-semibold">
              {detail.displayName}
            </h2>
            <span className="bg-muted text-muted-foreground shrink-0 rounded-md px-1.5 py-0.5 font-mono text-[11px]">
              v{detail.version}
            </span>
          </div>
          <p className="text-muted-foreground mt-0.5 line-clamp-1 text-xs">
            添加后将进入当前账号的个人技能库，并可在“我的技能”中管理
          </p>
        </div>

        {installation?.installed ? (
          <label className="border-border bg-background text-muted-foreground flex h-9 shrink-0 items-center gap-2 rounded-lg border px-2.5 text-xs font-medium">
            <PowerIcon className="size-3.5" />
            {installation.enabled ? "已启用" : "已停用"}
            <Switch
              checked={installation.enabled}
              disabled={mutating}
              aria-label={installation.enabled ? "停用技能" : "启用技能"}
              onCheckedChange={onToggle}
            />
          </label>
        ) : null}

        <Button
          type="button"
          size="sm"
          disabled={loading || mutating || Boolean(current)}
          onClick={onInstall}
          className="min-w-36"
        >
          {loading || mutating ? (
            <LoaderCircleIcon className="animate-spin" />
          ) : current ? (
            <CheckCircle2Icon />
          ) : installation?.installed ? (
            <RefreshCwIcon />
          ) : (
            <PackageSearchIcon />
          )}
          {loading ? "读取版本…" : mutating ? "正在部署…" : installLabel}
        </Button>

        {installation?.installed ? (
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            disabled={mutating}
            aria-label="卸载技能"
            title="卸载"
            onClick={onUninstall}
          >
            <Trash2Icon />
          </Button>
        ) : null}
      </div>
    </section>
  );
}

function replaceRuntimeSkill(
  skills: SkillHubRuntimeSkill[],
  next: SkillHubRuntimeSkill,
) {
  const key = `${next.namespace}/${next.slug}`;
  return [
    ...skills.filter((skill) => `${skill.namespace}/${skill.slug}` !== key),
    next,
  ];
}

function replaceSkillEnabled(
  skills: Skill[] | undefined,
  skillName: string,
  enabled: boolean,
) {
  return skills?.map((skill) =>
    skill.name === skillName ? { ...skill, enabled } : skill,
  );
}

export default function SkillCenterPage() {
  const { t } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { setOpen, setOpenMobile } = useSidebar();
  const didCollapseSidebar = useRef(false);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const lastFramePathRef = useRef<string>(SKILLHUB_EMBED_PATH);
  const frameSessionRecoveryRef = useRef(0);
  const [skillHubEnabled, setSkillHubEnabled] = useState<boolean | null>(null);
  const [sidebarInitialized, setSidebarInitialized] = useState(false);
  const [sessionState, setSessionState] =
    useState<SkillHubSessionState>("idle");
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [sessionAttempt, setSessionAttempt] = useState(0);
  const [frameReady, setFrameReady] = useState(false);
  const [framePath, setFramePath] = useState<string>(SKILLHUB_EMBED_PATH);
  const [activeFramePath, setActiveFramePath] =
    useState<string>(SKILLHUB_EMBED_PATH);
  const [showMySkills, setShowMySkills] = useState(false);
  const [skillHubUser, setSkillHubUser] = useState<SkillHubUser | null>(null);
  const [hasNamespaceReviewAccess, setHasNamespaceReviewAccess] =
    useState(false);
  const [skillDetail, setSkillDetail] = useState<SkillHubDetail | null>(null);
  const [runtimeSkills, setRuntimeSkills] = useState<SkillHubRuntimeSkill[]>(
    [],
  );
  const [runtimeLoading, setRuntimeLoading] = useState(false);
  const [runtimeLoadError, setRuntimeLoadError] = useState<string | null>(null);
  const [runtimeMutating, setRuntimeMutating] = useState(false);
  const {
    skills,
    isLoading: skillsLoading,
    error: skillsError,
  } = useSkills({ enabled: showMySkills && skillHubEnabled === true });
  const { mutate: enableCustomSkill, isPending: customSkillMutating } =
    useEnableSkill();

  const loadSkillHubUser = useCallback(async (): Promise<SkillHubUserProbe> => {
    if (skillHubEnabled !== true) return "unavailable";
    try {
      const response = await fetch(SKILLHUB_AUTH_ME_PATH, {
        credentials: "include",
        cache: "no-store",
      });
      if (!response.ok) {
        if (response.status === 401) {
          setSkillHubUser(null);
          return "unauthorized";
        }
        return "unavailable";
      }
      const nextUser = readSkillHubUser(await response.json());
      if (!nextUser) return "unavailable";
      setSkillHubUser(nextUser);
      return "authenticated";
    } catch {
      return "unavailable";
    }
  }, [skillHubEnabled]);

  useEffect(() => {
    let active = true;
    void fetch("/api/runtime-config", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((config: { skillHubEnabled?: boolean } | null) => {
        if (active) setSkillHubEnabled(config?.skillHubEnabled === true);
      })
      .catch(() => {
        if (active) setSkillHubEnabled(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (skillHubEnabled !== true) {
      if (skillHubEnabled === false) setSidebarInitialized(true);
      return;
    }
    if (didCollapseSidebar.current) {
      return;
    }
    didCollapseSidebar.current = true;
    setOpen(false);
    setOpenMobile(false);
    setSidebarInitialized(true);
  }, [setOpen, setOpenMobile, skillHubEnabled]);

  useEffect(() => {
    if (skillHubEnabled !== true || !user?.id) {
      if (skillHubEnabled === true) {
        setSessionState("idle");
        setSessionError(null);
        setFrameReady(false);
        setSkillHubUser(null);
      }
      return;
    }

    const controller = new AbortController();
    setSessionState("bootstrapping");
    setSessionError(null);
    setFrameReady(false);
    setSkillHubUser(null);

    void bootstrapSkillHubSession(controller.signal)
      .then((nextUser) => {
        if (controller.signal.aborted) return;
        setSkillHubUser(nextUser);
        setSessionState("ready");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setSessionState("error");
        setSessionError(
          error instanceof SkillHubSessionBootstrapError
            ? error.message
            : "技能中心登录失败，请稍后重试。",
        );
      });

    return () => controller.abort();
  }, [sessionAttempt, skillHubEnabled, user?.id]);

  useEffect(() => {
    if (sessionState !== "ready" || !skillHubUser?.userId) {
      setHasNamespaceReviewAccess(false);
      return;
    }

    const controller = new AbortController();
    void loadSkillHubManagedNamespaces(controller.signal)
      .then((namespaces) => {
        if (!controller.signal.aborted) {
          setHasNamespaceReviewAccess(
            hasSkillHubNamespaceReviewAccess(namespaces),
          );
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setHasNamespaceReviewAccess(false);
      });
    return () => controller.abort();
  }, [sessionState, skillHubUser?.userId]);

  useEffect(() => {
    frameSessionRecoveryRef.current = 0;
  }, [user?.id]);

  const access = useMemo<SkillHubAccess>(
    () => ({
      skillAdmin: hasSkillHubRole(skillHubUser, "SKILL_ADMIN", "SUPER_ADMIN"),
      userAdmin: hasSkillHubRole(skillHubUser, "USER_ADMIN", "SUPER_ADMIN"),
      auditor: hasSkillHubRole(skillHubUser, "AUDITOR", "SUPER_ADMIN"),
      superAdmin: hasSkillHubRole(skillHubUser, "SUPER_ADMIN"),
      governance: hasSkillHubRole(
        skillHubUser,
        "SKILL_ADMIN",
        "NAMESPACE_ADMIN",
        "SUPER_ADMIN",
      ),
    }),
    [skillHubUser],
  );

  const hasManagementAccess =
    access.skillAdmin ||
    access.userAdmin ||
    access.auditor ||
    access.governance ||
    hasNamespaceReviewAccess;
  const workspaceActive =
    !showMySkills &&
    Array.from(WORKSPACE_PATHS).some((path) =>
      path === SKILLHUB_ROUTES.dashboard
        ? activeFramePath === path
        : activeFramePath.startsWith(path),
    );

  const skillLocation = useMemo(
    () =>
      skillHubEnabled === true && !showMySkills
        ? readSkillHubSkillLocation(activeFramePath)
        : null,
    [activeFramePath, showMySkills, skillHubEnabled],
  );
  const installation = useMemo(() => {
    if (!skillLocation) return null;
    return (
      runtimeSkills.find(
        (skill) =>
          skill.namespace === skillLocation.namespace &&
          skill.slug === skillLocation.slug,
      ) ?? null
    );
  }, [runtimeSkills, skillLocation]);

  const navigateSkillHub = useCallback(
    (path: string) => {
      setShowMySkills(false);
      if (activeFramePath === path) {
        if (sessionState === "ready") setFrameReady(true);
        return;
      }
      setFrameReady(false);
      setFramePath(path);
      lastFramePathRef.current = path;
      setActiveFramePath(path);
      try {
        frameRef.current?.contentWindow?.location.assign(path);
      } catch {
        // The iframe src state remains the fallback navigation mechanism.
      }
    },
    [activeFramePath, sessionState],
  );

  const openMySkills = useCallback(() => {
    setShowMySkills(true);
    setSkillDetail(null);
  }, []);

  const handleFrameLoad = useCallback(() => {
    const frame = frameRef.current;
    if (frame) {
      applySkillHubEmbeddedTheme(frame);
      try {
        const location = frame.contentWindow?.location;
        if (location) {
          const nextPath = `${location.pathname}${location.search}`;
          lastFramePathRef.current = nextPath;
          setActiveFramePath(nextPath);
        }
      } catch {
        // The unified proxy is same-origin in supported deployments.
      }
    }
    void loadSkillHubUser().then((status) => {
      if (status === "authenticated") {
        frameSessionRecoveryRef.current = 0;
        setFrameReady(true);
        return;
      }

      setFrameReady(false);
      if (status === "unauthorized" && frameSessionRecoveryRef.current < 1) {
        frameSessionRecoveryRef.current += 1;
        setSessionAttempt((attempt) => attempt + 1);
        return;
      }

      setSessionState("error");
      setSessionError(
        status === "unauthorized"
          ? "技能中心会话未能建立，请重试登录。"
          : "技能中心暂时无法验证账号信息，请稍后重试。",
      );
    });
  }, [loadSkillHubUser]);

  useEffect(() => {
    if (skillHubEnabled !== true) return;
    const syncFramePath = () => {
      try {
        const location = frameRef.current?.contentWindow?.location;
        if (!location) return;
        const nextPath = `${location.pathname}${location.search}`;
        if (nextPath !== lastFramePathRef.current) {
          lastFramePathRef.current = nextPath;
          setActiveFramePath(nextPath);
          applySkillHubEmbeddedTheme(frameRef.current!);
        }
      } catch {
        // A cross-origin frame is unsupported and simply cannot be enhanced.
      }
    };
    const timer = window.setInterval(syncFramePath, 500);
    return () => window.clearInterval(timer);
  }, [skillHubEnabled]);

  useEffect(() => {
    const namespace = skillLocation?.namespace;
    const slug = skillLocation?.slug;
    if (!namespace || !slug) {
      setSkillDetail(null);
      setRuntimeLoading(false);
      return;
    }

    let cancelled = false;
    setRuntimeLoading(true);
    void Promise.all([
      loadSkillHubDetail({ namespace, slug }),
      loadSkillHubRuntimeSkills(),
    ])
      .then(([detail, skills]) => {
        if (cancelled) return;
        setSkillDetail(detail);
        setRuntimeSkills(skills);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setSkillDetail(null);
        toast.error(
          error instanceof Error ? error.message : "无法读取技能部署状态",
        );
      })
      .finally(() => {
        if (!cancelled) setRuntimeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [skillLocation?.namespace, skillLocation?.slug]);

  useEffect(() => {
    if (!showMySkills || skillHubEnabled !== true) return;
    let cancelled = false;
    setRuntimeLoading(true);
    setRuntimeLoadError(null);
    void loadSkillHubRuntimeSkills()
      .then((items) => {
        if (!cancelled) setRuntimeSkills(items);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setRuntimeLoadError(
          error instanceof Error ? error.message : "无法读取已安装技能",
        );
      })
      .finally(() => {
        if (!cancelled) setRuntimeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showMySkills, skillHubEnabled]);

  const handleRuntimeInstall = useCallback(async () => {
    if (!skillDetail || runtimeMutating) return;
    setRuntimeMutating(true);
    try {
      const result = await installSkillHubRuntimeSkill(skillDetail);
      setRuntimeSkills((current) => replaceRuntimeSkill(current, result));
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      toast.success(
        result.action === "upgraded"
          ? `已升级 ${skillDetail.displayName} 到 v${result.version}`
          : `已将 ${skillDetail.displayName} 添加到我的技能`,
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "技能部署失败");
    } finally {
      setRuntimeMutating(false);
    }
  }, [queryClient, runtimeMutating, skillDetail]);

  const handleRuntimeToggle = useCallback(
    async (target: SkillHubRuntimeSkill, enabled: boolean) => {
      if (runtimeMutating) return;
      setRuntimeMutating(true);
      try {
        const result = await setSkillHubRuntimeEnabled(target, enabled);
        setRuntimeSkills((current) => replaceRuntimeSkill(current, result));
        queryClient.setQueryData<Skill[]>(["skills"], (current) =>
          replaceSkillEnabled(current, result.skill_name, result.enabled),
        );
        void queryClient.invalidateQueries({ queryKey: ["skills"] });
        toast.success(enabled ? "技能已启用" : "技能已停用");
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : "技能状态更新失败",
        );
      } finally {
        setRuntimeMutating(false);
      }
    },
    [queryClient, runtimeMutating],
  );

  const handleRuntimeUninstall = useCallback(
    async (target: SkillHubRuntimeSkill, displayName: string) => {
      if (runtimeMutating) return;
      if (!window.confirm(`确认卸载“${displayName}”？`)) return;
      setRuntimeMutating(true);
      try {
        await uninstallSkillHubRuntimeSkill(target);
        setRuntimeSkills((current) =>
          current.filter(
            (skill) =>
              !(
                skill.namespace === target.namespace &&
                skill.slug === target.slug
              ),
          ),
        );
        await queryClient.invalidateQueries({ queryKey: ["skills"] });
        toast.success("技能已从我的技能中移除");
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "技能卸载失败");
      } finally {
        setRuntimeMutating(false);
      }
    },
    [queryClient, runtimeMutating],
  );

  const handleCustomToggle = useCallback(
    (skill: Skill, enabled: boolean) => {
      enableCustomSkill(
        { skillName: skill.name, enabled },
        {
          onSuccess: () => toast.success(enabled ? "技能已启用" : "技能已停用"),
          onError: (error) =>
            toast.error(
              error instanceof Error ? error.message : "技能状态更新失败",
            ),
        },
      );
    },
    [enableCustomSkill],
  );

  if (skillHubEnabled !== true) {
    return (
      <main className="app-scroll-region bg-background flex h-full min-h-0 min-w-0 items-center justify-center overflow-hidden p-6">
        <section className="border-border bg-card flex w-full max-w-lg flex-col items-center rounded-2xl border px-6 py-10 text-center shadow-sm">
          <span className="bg-primary/10 text-primary flex size-12 items-center justify-center rounded-xl">
            {skillHubEnabled === null ? (
              <LoaderCircleIcon className="size-5 animate-spin" />
            ) : (
              <PackageSearchIcon className="size-5" />
            )}
          </span>
          <h1 className="text-foreground mt-4 text-lg font-semibold">
            {skillHubEnabled === null
              ? "正在检查技能中心…"
              : "技能中心尚未开放"}
          </h1>
          {skillHubEnabled === false ? (
            <>
              <p className="text-muted-foreground mt-2 max-w-sm text-sm leading-6">
                当前环境尚未完成技能中心的认证与安全验收，其他工作区功能不受影响。
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-5"
                onClick={() => router.replace("/workspace/chats/new")}
              >
                返回新对话
              </Button>
            </>
          ) : null}
        </section>
      </main>
    );
  }

  if (sessionState !== "ready") {
    const sessionFailed = sessionState === "error";
    return (
      <main className="app-scroll-region bg-background flex h-full min-h-0 min-w-0 items-center justify-center overflow-hidden p-4 md:p-6">
        <section
          data-testid="skillhub-session-state"
          data-state={sessionState}
          className="border-border bg-card flex w-full max-w-lg flex-col items-center rounded-2xl border px-6 py-10 text-center shadow-sm"
        >
          <span className="bg-primary/10 text-primary flex size-12 items-center justify-center rounded-xl">
            {sessionFailed ? (
              <ShieldCheckIcon className="size-5" />
            ) : (
              <LoaderCircleIcon className="size-5 animate-spin" />
            )}
          </span>
          <h1 className="text-foreground mt-4 text-lg font-semibold">
            {sessionFailed
              ? "技能中心登录未完成"
              : sessionState === "idle"
                ? "正在确认平台登录…"
                : "正在同步技能中心账号…"}
          </h1>
          <p className="text-muted-foreground mt-2 max-w-sm text-sm leading-6">
            {sessionFailed
              ? (sessionError ?? "技能中心暂时不可用，请稍后重试。")
              : sessionState === "idle"
                ? "正在读取当前登录账号，确认后会自动进入技能中心。"
                : "首次进入会自动创建账号；后续进入会同步当前姓名和账号资料。"}
          </p>
          {sessionFailed ? (
            <div className="mt-5 flex flex-wrap justify-center gap-2">
              <Button
                type="button"
                size="sm"
                onClick={() => {
                  frameSessionRecoveryRef.current = 0;
                  setSessionAttempt((attempt) => attempt + 1);
                }}
              >
                <RefreshCwIcon />
                重试登录
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => router.replace("/workspace/chats/new")}
              >
                返回新对话
              </Button>
            </div>
          ) : null}
        </section>
      </main>
    );
  }

  return (
    <main className="cnooc-skill-center app-scroll-region bg-background relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
      <header className="cnooc-skill-header border-border/80 bg-card/95 relative z-30 shrink-0 border-b px-3 py-3 shadow-[0_1px_0_rgba(15,55,92,0.03)] backdrop-blur md:px-5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 lg:flex-nowrap">
          <div className="flex min-w-0 flex-1 items-center lg:flex-none">
            <ModulePageTitle
              icon={PackageSearchIcon}
              title={t.sidebar.skillCenter}
            />
          </div>

          <nav
            aria-label="技能中心导航"
            className="order-last flex min-w-0 basis-full items-center gap-1 overflow-x-auto overscroll-x-contain [scrollbar-width:none] lg:order-none lg:ml-3 lg:flex-1 lg:basis-auto [&::-webkit-scrollbar]:hidden"
          >
            {PERSONAL_NAV_ITEMS.slice(0, 1).map((item) => (
              <SkillCenterNavButton
                key={item.path}
                label={item.label}
                icon={item.icon}
                active={
                  !showMySkills &&
                  activeFramePath.startsWith(item.path.split("?")[0]!)
                }
                onClick={() => navigateSkillHub(item.path)}
              />
            ))}

            <SkillCenterNavButton
              label="我的技能"
              icon={LibraryIcon}
              active={showMySkills}
              onClick={openMySkills}
            />

            {PERSONAL_NAV_ITEMS.slice(1).map((item) => (
              <SkillCenterNavButton
                key={item.path}
                label={item.label}
                icon={item.icon}
                active={
                  !showMySkills &&
                  activeFramePath.startsWith(item.path.split("?")[0]!)
                }
                onClick={() => navigateSkillHub(item.path)}
              />
            ))}

            <div className="hidden shrink-0 lg:block">
              <SkillHubWorkspaceMenu
                access={access}
                user={skillHubUser}
                hasNamespaceReviewAccess={hasNamespaceReviewAccess}
                active={workspaceActive}
                onNavigate={navigateSkillHub}
              />
            </div>
          </nav>

          {hasManagementAccess ? (
            <span className="border-primary/20 bg-primary/10 text-primary hidden shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium xl:inline-flex">
              管理员视图
            </span>
          ) : null}
          <div className="shrink-0 lg:hidden">
            <SkillHubWorkspaceMenu
              access={access}
              user={skillHubUser}
              hasNamespaceReviewAccess={hasNamespaceReviewAccess}
              active={workspaceActive}
              compact
              onNavigate={navigateSkillHub}
            />
          </div>
          {sidebarInitialized ? (
            <SidebarTrigger className="bg-card size-11 shrink-0 border shadow-sm md:hidden" />
          ) : null}
        </div>
      </header>

      {skillDetail ? (
        <SkillRuntimeBar
          detail={skillDetail}
          installation={installation}
          loading={runtimeLoading}
          mutating={runtimeMutating}
          onInstall={() => void handleRuntimeInstall()}
          onToggle={(enabled) =>
            installation
              ? void handleRuntimeToggle(installation, enabled)
              : undefined
          }
          onUninstall={() =>
            installation
              ? void handleRuntimeUninstall(
                  installation,
                  skillDetail.displayName,
                )
              : undefined
          }
        />
      ) : null}

      <div className="relative min-h-0 flex-1">
        {showMySkills ? (
          <MySkillsPanel
            skills={skills}
            installations={runtimeSkills}
            loading={skillsLoading || runtimeLoading}
            errorMessage={
              runtimeLoadError ??
              (skillsError instanceof Error ? skillsError.message : null)
            }
            isPlatformAdmin={user?.system_role === "admin"}
            mutating={runtimeMutating || customSkillMutating}
            onCreate={() => router.push("/workspace/chats/new?mode=skill")}
            onOpenDetail={(target) =>
              navigateSkillHub(
                skillHubDetailPath(target.namespace, target.slug),
              )
            }
            onToggleCustom={handleCustomToggle}
            onToggleInstalled={(target, enabled) =>
              void handleRuntimeToggle(target, enabled)
            }
            onUninstall={(target, displayName) =>
              void handleRuntimeUninstall(target, displayName)
            }
          />
        ) : null}
        {!showMySkills && !frameReady ? (
          <div className="bg-background absolute inset-0 z-20 flex items-center justify-center">
            <div className="text-muted-foreground flex items-center gap-3 text-sm">
              <span className="border-primary/25 border-t-primary size-4 animate-spin rounded-full border-2" />
              正在进入技能中心…
            </div>
          </div>
        ) : null}
        <iframe
          ref={frameRef}
          data-testid="skillhub-workspace-frame"
          title={`${t.sidebar.skillCenter}内容`}
          src={framePath}
          onLoad={handleFrameLoad}
          className={cn(
            "bg-background block h-full min-h-0 w-full min-w-0 border-0 transition-opacity duration-150",
            showMySkills ? "hidden" : frameReady ? "opacity-100" : "opacity-0",
          )}
          allow="clipboard-read; clipboard-write"
          referrerPolicy="same-origin"
        />
      </div>
    </main>
  );
}
