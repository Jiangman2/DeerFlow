"use client";

import {
  ActivityIcon,
  BotIcon,
  DatabaseIcon,
  ExternalLinkIcon,
  FileTextIcon,
  MessageSquareIcon,
  ScanTextIcon,
  ShieldIcon,
  Share2Icon,
  Trash2Icon,
  Undo2Icon,
  UserIcon,
  type LucideIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { type ComponentProps, type ReactElement, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  recordExternalAgentClick,
  useDeleteAgent,
  usePublishAgent,
  useUnpublishTeamAgent,
} from "@/core/agents";
import type { Agent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface AgentCardProps {
  agent: Agent;
  showDelete?: boolean;
  showPublish?: boolean;
  showUnpublish?: boolean;
}

type AgentVisualConfig = {
  cardClassName: string;
  accentBarClassName: string;
  avatarClassName: string;
  avatarUrl: string;
  domainIcon: LucideIcon;
};

const EXTERNAL_AGENT_VISUAL_CONFIG: Record<string, AgentVisualConfig> = {
  "http://10.57.128.79:6000/": {
    cardClassName:
      "border-cyan-200/70 from-cyan-50/80 via-background to-blue-50/55 hover:border-cyan-400/60 hover:shadow-cyan-500/10",
    accentBarClassName: "from-cyan-400/45 via-cyan-300/10 to-blue-400/35",
    avatarClassName:
      "bg-[linear-gradient(145deg,rgba(6,182,212,0.24),rgba(37,99,235,0.16))] text-cyan-800 ring-cyan-500/25",
    avatarUrl: "/images/experts/expert-analysis.svg",
    domainIcon: ActivityIcon,
  },
  "http://10.57.128.79:7860/": {
    cardClassName:
      "border-amber-200/70 from-amber-50/75 via-background to-orange-50/50 hover:border-amber-400/60 hover:shadow-amber-500/10",
    accentBarClassName: "from-amber-400/45 via-amber-300/10 to-orange-400/35",
    avatarClassName:
      "bg-[linear-gradient(145deg,rgba(245,158,11,0.24),rgba(249,115,22,0.16))] text-amber-800 ring-amber-500/25",
    avatarUrl: "/images/experts/expert-report.svg",
    domainIcon: FileTextIcon,
  },
  "http://10.57.128.79:8100/": {
    cardClassName:
      "border-blue-200/70 from-blue-50/80 via-background to-sky-50/55 hover:border-blue-400/60 hover:shadow-blue-500/10",
    accentBarClassName: "from-blue-400/45 via-blue-300/10 to-sky-400/35",
    avatarClassName:
      "bg-[linear-gradient(145deg,rgba(59,130,246,0.24),rgba(14,165,233,0.16))] text-blue-800 ring-blue-500/25",
    avatarUrl: "/images/experts/expert-analysis.svg",
    domainIcon: ScanTextIcon,
  },
  "http://10.57.128.79:5000/": {
    cardClassName:
      "border-violet-200/70 from-violet-50/75 via-background to-fuchsia-50/45 hover:border-violet-400/60 hover:shadow-violet-500/10",
    accentBarClassName:
      "from-violet-400/45 via-violet-300/10 to-fuchsia-400/35",
    avatarClassName:
      "bg-[linear-gradient(145deg,rgba(139,92,246,0.24),rgba(217,70,239,0.16))] text-violet-800 ring-violet-500/25",
    avatarUrl: "/images/experts/expert-data.svg",
    domainIcon: DatabaseIcon,
  },
  "http://10.57.128.79:5001/": {
    cardClassName:
      "border-emerald-200/70 from-emerald-50/75 via-background to-green-50/50 hover:border-emerald-400/60 hover:shadow-emerald-500/10",
    accentBarClassName:
      "from-emerald-400/45 via-emerald-300/10 to-green-400/35",
    avatarClassName:
      "bg-[linear-gradient(145deg,rgba(16,185,129,0.25),rgba(34,197,94,0.16))] text-emerald-800 ring-emerald-500/25",
    avatarUrl: "/images/experts/expert-review.svg",
    domainIcon: ShieldIcon,
  },
};

const EXTERNAL_AGENT_PROXY_PATHS: Record<string, string> = {
  "http://10.57.128.79:8100": "/agent-tools/doc-parse/",
  "http://10.57.128.79:6000": "/agent-tools/seismic-resolution/",
  "http://10.57.128.79:5001": "/agent-tools/report-review/",
  "http://10.57.128.79:5000": "/agent-tools/auto-data-collection/",
  "http://10.57.128.79:7860": "/agent-tools/report-writing/",
};

function buildExternalAgentHref(rawUrl: string) {
  try {
    const url = new URL(rawUrl);
    const proxyBasePath = EXTERNAL_AGENT_PROXY_PATHS[url.origin];

    if (!proxyBasePath) {
      return rawUrl;
    }

    const normalizedBasePath = proxyBasePath.replace(/\/?$/, "/");
    const pathSuffix =
      url.pathname === "/" ? "" : url.pathname.replace(/^\/+/, "");

    return `${normalizedBasePath}${pathSuffix}${url.search}${url.hash}`;
  } catch {
    return rawUrl;
  }
}

function TruncatedTooltip({
  text,
  children,
}: {
  text: string;
  children: ReactElement;
}) {
  const [truncated, setTruncated] = useState(false);
  return (
    <Tooltip>
      <TooltipTrigger
        asChild
        onPointerEnter={(e) => {
          const el = e.currentTarget;
          setTruncated(
            el.scrollWidth > el.clientWidth ||
              el.scrollHeight > el.clientHeight,
          );
        }}
      >
        {children}
      </TooltipTrigger>
      {truncated && (
        <TooltipContent className="max-w-xs text-wrap break-words">
          {text}
        </TooltipContent>
      )}
    </Tooltip>
  );
}

function TruncatedBadge({
  label,
  variant,
  className,
}: {
  label: string;
  variant: ComponentProps<typeof Badge>["variant"];
  className?: string;
}) {
  return (
    <TruncatedTooltip text={label}>
      <Badge
        variant={variant}
        className={cn("block max-w-full truncate", className)}
      >
        {label}
      </Badge>
    </TruncatedTooltip>
  );
}

export function AgentCard({
  agent,
  showDelete = true,
  showPublish = false,
  showUnpublish = false,
}: AgentCardProps) {
  const { t } = useI18n();
  const router = useRouter();
  const deleteAgent = useDeleteAgent();
  const publishAgent = usePublishAgent();
  const unpublishTeamAgent = useUnpublishTeamAgent();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [unpublishOpen, setUnpublishOpen] = useState(false);
  const externalUrl = agent.external_url?.trim();
  const isExternalAgent = Boolean(externalUrl);
  const externalHref = externalUrl
    ? buildExternalAgentHref(externalUrl)
    : undefined;
  const displayConfig = externalUrl
    ? EXTERNAL_AGENT_VISUAL_CONFIG[externalUrl]
    : undefined;
  const DomainIcon = displayConfig?.domainIcon;

  function recordExternalClick() {
    if (!externalUrl) return;
    void recordExternalAgentClick(agent.name).catch(() => undefined);
  }

  function handleChat() {
    router.push(
      `/workspace/agents/${encodeURIComponent(agent.name)}/chats/new`,
    );
  }

  async function handleDelete() {
    try {
      await deleteAgent.mutateAsync(agent.name);
      toast.success(t.agents.deleteSuccess);
      setDeleteOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handlePublish() {
    try {
      await publishAgent.mutateAsync(agent.name);
      toast.success(t.agents.publishSuccess);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleUnpublish() {
    try {
      await unpublishTeamAgent.mutateAsync(agent.name);
      toast.success(t.agents.unpublishSuccess);
      setUnpublishOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  const hasCapabilities =
    (agent.tool_groups?.length ?? 0) > 0 || (agent.skills?.length ?? 0) > 0;

  return (
    <>
      <Card
        className={cn(
          "group relative flex h-full flex-col overflow-hidden border bg-gradient-to-br shadow-sm transition-all duration-300 hover:-translate-y-0.5 hover:shadow-lg",
          displayConfig?.cardClassName ??
            "from-background to-muted/30 hover:border-primary/30 hover:shadow-primary/10",
        )}
      >
        <div
          className={cn(
            "pointer-events-none absolute inset-x-0 top-0 h-1.5 rounded-t-xl bg-gradient-to-r via-transparent to-transparent opacity-80",
            displayConfig?.accentBarClassName ?? "from-primary/12",
          )}
        />
        <CardHeader className="pb-3">
          <div className="flex min-w-0 items-start justify-between gap-2">
            <div className="flex min-w-0 items-start gap-2">
              {displayConfig ? (
                <div
                  className={cn(
                    "relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl shadow-sm ring-1 transition-transform duration-300 group-hover:scale-105",
                    displayConfig.avatarClassName,
                  )}
                  aria-label={agent.name}
                >
                  <img
                    src={displayConfig.avatarUrl}
                    alt={agent.name}
                    className="h-7 w-7 rounded-full border border-white/70 object-cover shadow-sm"
                    loading="lazy"
                    onError={(e) => {
                      const target = e.currentTarget;
                      target.style.display = "none";
                      target.nextElementSibling?.classList.remove("hidden");
                    }}
                  />
                  <span className="bg-background/90 hidden h-7 w-7 items-center justify-center rounded-full border border-white/60 shadow-sm">
                    <UserIcon className="h-4 w-4" />
                  </span>
                  {DomainIcon && (
                    <span className="bg-background text-foreground absolute -right-1 -bottom-1 flex h-4.5 w-4.5 items-center justify-center rounded-full border shadow-sm">
                      <DomainIcon className="h-2.5 w-2.5" />
                    </span>
                  )}
                </div>
              ) : (
                <div className="bg-primary text-primary-foreground ring-primary/20 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl shadow-sm ring-1 transition-transform duration-300 group-hover:scale-105">
                  <BotIcon className="h-5 w-5" />
                </div>
              )}
              <div className="min-w-0 flex-1">
                <TruncatedTooltip text={agent.name}>
                  <CardTitle
                    className={cn(
                      "text-sm leading-6 tracking-tight sm:text-base",
                      isExternalAgent
                        ? "line-clamp-2 break-words whitespace-normal"
                        : "truncate whitespace-nowrap",
                    )}
                  >
                    {agent.name}
                  </CardTitle>
                </TruncatedTooltip>
                {agent.model && (
                  <TruncatedBadge
                    label={agent.model}
                    variant="secondary"
                    className="bg-muted/70 mt-1 rounded-md text-xs"
                  />
                )}
              </div>
            </div>
          </div>
          {agent.description && (
            <TruncatedTooltip text={agent.description}>
              <CardDescription className="text-muted-foreground/90 mt-2 line-clamp-2 text-sm leading-relaxed break-all">
                {agent.description}
              </CardDescription>
            </TruncatedTooltip>
          )}
        </CardHeader>

        {hasCapabilities && (
          <CardContent className="pt-0 pb-3">
            <div className="flex flex-wrap gap-1">
              {agent.tool_groups?.map((group) => (
                <TruncatedBadge
                  key={`tg:${group}`}
                  label={group}
                  variant="outline"
                  className="bg-background/90 rounded-md text-xs"
                />
              ))}
              {agent.skills?.map((skill) => (
                <TruncatedBadge
                  key={`sk:${skill}`}
                  label={skill}
                  variant="secondary"
                  className="bg-muted/70 rounded-md text-xs"
                />
              ))}
            </div>
          </CardContent>
        )}

        <CardFooter className="mt-auto flex items-center justify-between gap-2 border-t pt-3">
          {externalUrl ? (
            <Button
              asChild
              size="sm"
              className="flex-1 rounded-lg shadow-sm transition-all duration-300 group-hover:shadow"
            >
              <a
                href={externalHref}
                target="_blank"
                rel="noreferrer"
                onClick={recordExternalClick}
              >
                <ExternalLinkIcon className="mr-1.5 h-3.5 w-3.5" />
                {t.agents.openExternal}
              </a>
            </Button>
          ) : (
            <Button
              size="sm"
              className="flex-1 rounded-lg shadow-sm transition-all duration-300 group-hover:shadow"
              onClick={handleChat}
            >
              <MessageSquareIcon className="mr-1.5 h-3.5 w-3.5" />
              {t.agents.chat}
            </Button>
          )}
          {(showPublish || showUnpublish || showDelete) && (
            <div className="flex gap-1">
              {showPublish && (
                <Button
                  size="icon"
                  variant="ghost"
                  className="text-primary hover:text-primary hover:bg-primary/10 h-8 w-8 shrink-0 rounded-lg transition-colors"
                  onClick={() => void handlePublish()}
                  disabled={publishAgent.isPending}
                  title={t.agents.publishToSquare}
                >
                  <Share2Icon className="h-3.5 w-3.5" />
                </Button>
              )}
              {showUnpublish && (
                <Button
                  size="icon"
                  variant="ghost"
                  className="text-muted-foreground hover:text-foreground hover:bg-muted h-8 w-8 shrink-0 rounded-lg transition-colors"
                  onClick={() => setUnpublishOpen(true)}
                  disabled={unpublishTeamAgent.isPending}
                  title={t.agents.unpublishFromSquare}
                >
                  <Undo2Icon className="h-3.5 w-3.5" />
                </Button>
              )}
              {showDelete && (
                <Button
                  size="icon"
                  variant="ghost"
                  className="text-destructive hover:text-destructive h-8 w-8 shrink-0 rounded-lg transition-colors hover:bg-red-500/10"
                  onClick={() => setDeleteOpen(true)}
                  title={t.agents.delete}
                >
                  <Trash2Icon className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
          )}
        </CardFooter>
      </Card>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.agents.delete}</DialogTitle>
            <DialogDescription>{t.agents.deleteConfirm}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteOpen(false)}
              disabled={deleteAgent.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleteAgent.isPending}
            >
              {deleteAgent.isPending ? t.common.loading : t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={unpublishOpen} onOpenChange={setUnpublishOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.agents.unpublishFromSquare}</DialogTitle>
            <DialogDescription>{t.agents.unpublishConfirm}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setUnpublishOpen(false)}
              disabled={unpublishTeamAgent.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleUnpublish()}
              disabled={unpublishTeamAgent.isPending}
            >
              {unpublishTeamAgent.isPending
                ? t.common.loading
                : t.agents.unpublishFromSquare}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
