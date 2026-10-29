"use client";

import {
  ExternalLinkIcon,
  EyeIcon,
  FlameIcon,
  MessageSquareIcon,
  MoreHorizontalIcon,
  PencilIcon,
  Share2Icon,
  Trash2Icon,
  Undo2Icon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  recordSquareEmployeeLaunch,
  resolveExternalAgentLaunchHref,
  useDeleteAgent,
  usePublishAgent,
  useUnpublishTeamAgent,
} from "@/core/agents";
import type { Agent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { DigitalEmployeeAvatar } from "./digital-employee-avatar";

interface AgentCardProps {
  agent: Agent;
  categoryLabel?: string;
  showDelete?: boolean;
  showPublish?: boolean;
  showUnpublish?: boolean;
  onEdit?: (agent: Agent) => void;
  onToggleHot?: (agent: Agent) => void;
  hotPending?: boolean;
  trackSquareLaunch?: boolean;
}

export function AgentCard({
  agent,
  categoryLabel,
  showDelete = true,
  showPublish = false,
  showUnpublish = false,
  onEdit,
  onToggleHot,
  hotPending,
  trackSquareLaunch = false,
}: AgentCardProps) {
  const { t } = useI18n();
  const router = useRouter();
  const deleteAgent = useDeleteAgent();
  const publishAgent = usePublishAgent();
  const unpublishTeamAgent = useUnpublishTeamAgent();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [unpublishOpen, setUnpublishOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const externalUrl = agent.external_url?.trim();
  const externalHref = resolveExternalAgentLaunchHref(externalUrl);
  const configuredDisplayName = agent.display_name?.trim();
  const displayName = configuredDisplayName?.length
    ? configuredDisplayName
    : agent.name;
  const sourceLabel =
    agent.source === "personal" ? t.agents.personal : t.agents.enterprise;
  const capabilityLabels = [
    ...(agent.tags ?? []),
    ...(agent.tool_groups ?? []),
    ...(agent.skills ?? []),
    ...(agent.model ? [agent.model] : []),
  ].filter((value, index, values) => values.indexOf(value) === index);

  function recordLaunch() {
    if (trackSquareLaunch)
      void recordSquareEmployeeLaunch(agent).catch(() => undefined);
  }

  function handleChat() {
    recordLaunch();
    router.push(
      `/workspace/agents/${encodeURIComponent(agent.name)}/chats/new`,
    );
  }

  async function handleDelete() {
    try {
      await deleteAgent.mutateAsync(agent.name);
      toast.success(t.agents.deleteSuccess);
      setDeleteOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  async function handlePublish() {
    try {
      await publishAgent.mutateAsync(agent.name);
      toast.success(t.agents.publishSuccess);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleUnpublish() {
    try {
      await unpublishTeamAgent.mutateAsync(agent.name);
      toast.success(t.agents.unpublishSuccess);
      setUnpublishOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  const primaryAction = externalHref ? (
    <Button
      asChild
      size="sm"
      className="h-11 flex-1 rounded-lg px-3 sm:h-8 sm:flex-none"
    >
      <a
        href={externalHref}
        target="_blank"
        rel="noreferrer"
        onClick={recordLaunch}
      >
        <ExternalLinkIcon data-icon="inline-start" />
        {t.agents.openExternal}
      </a>
    </Button>
  ) : (
    <Button
      size="sm"
      className="h-11 flex-1 rounded-lg px-3 sm:h-8 sm:flex-none"
      onClick={handleChat}
    >
      <MessageSquareIcon data-icon="inline-start" />
      {t.agents.chat}
    </Button>
  );

  return (
    <>
      <article
        data-testid="digital-employee-card"
        className="bg-card hover:border-primary/45 hover:shadow-primary/5 group flex min-w-0 flex-col gap-3 rounded-xl border p-3.5 shadow-sm transition-[border-color,box-shadow] duration-200 sm:flex-row sm:items-center sm:gap-4 sm:px-4 sm:py-3"
      >
        <div className="grid min-w-0 flex-1 grid-cols-[56px_minmax(0,1fr)] items-center gap-x-3 gap-y-2.5 sm:flex sm:gap-3">
          <DigitalEmployeeAvatar agent={agent} className="size-14 sm:size-16" />
          <div className="contents sm:block sm:min-w-0 sm:flex-1">
            <div className="flex min-w-0 flex-wrap items-center gap-1.5">
              <h2 className="line-clamp-2 w-full max-w-full text-[15px] leading-5 font-semibold tracking-tight break-words sm:line-clamp-1 sm:w-auto">
                {displayName}
              </h2>
              <Badge
                variant="outline"
                className={cn(
                  "h-5 px-1.5 text-[10px] font-medium",
                  agent.source === "personal"
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300"
                    : "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300",
                )}
              >
                {sourceLabel}
              </Badge>
              {agent.is_hot && (
                <Badge data-testid="employee-hot-badge" variant="secondary">
                  <FlameIcon aria-hidden="true" />
                  {t.employeeDiscovery.hot}
                </Badge>
              )}
              {categoryLabel ? (
                <span className="text-muted-foreground text-[11px]">
                  · {categoryLabel}
                </span>
              ) : null}
            </div>
            <p className="text-muted-foreground col-span-2 line-clamp-2 text-[13px] leading-5 sm:mt-1 sm:line-clamp-1">
              {agent.description || t.agents.noDescription}
            </p>
            {capabilityLabels.length > 0 ? (
              <div className="col-span-2 flex min-w-0 gap-1 overflow-hidden sm:mt-1.5">
                {capabilityLabels.slice(0, 4).map((label) => (
                  <Badge
                    key={label}
                    variant="secondary"
                    className="h-5 max-w-28 shrink-0 truncate rounded-md px-1.5 text-[10px] font-normal"
                  >
                    {label}
                  </Badge>
                ))}
                {capabilityLabels.length > 4 ? (
                  <span className="text-muted-foreground self-center text-[10px]">
                    +{capabilityLabels.length - 4}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

        <div className="flex shrink-0 items-center justify-end gap-2 border-t pt-3 sm:gap-1.5 sm:border-0 sm:pt-0">
          {primaryAction}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t.agents.more}
                className="size-11 rounded-lg sm:size-8"
              >
                <MoreHorizontalIcon />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44">
              <DropdownMenuGroup>
                {onToggleHot && (
                  <DropdownMenuItem
                    disabled={hotPending}
                    data-testid="employee-toggle-hot"
                    onSelect={() => onToggleHot(agent)}
                  >
                    <FlameIcon />
                    {agent.is_hot
                      ? t.employeeDiscovery.unmarkHot
                      : t.employeeDiscovery.markHot}
                  </DropdownMenuItem>
                )}
                <DropdownMenuItem onSelect={() => setProfileOpen(true)}>
                  <EyeIcon />
                  {t.agents.viewProfile}
                </DropdownMenuItem>
                {onEdit ? (
                  <DropdownMenuItem onSelect={() => onEdit(agent)}>
                    <PencilIcon />
                    {t.agents.edit}
                  </DropdownMenuItem>
                ) : null}
                {showPublish ? (
                  <DropdownMenuItem
                    disabled={publishAgent.isPending}
                    onSelect={() => void handlePublish()}
                  >
                    <Share2Icon />
                    {t.agents.publishToSquare}
                  </DropdownMenuItem>
                ) : null}
                {showUnpublish ? (
                  <DropdownMenuItem onSelect={() => setUnpublishOpen(true)}>
                    <Undo2Icon />
                    {t.agents.unpublishFromSquare}
                  </DropdownMenuItem>
                ) : null}
                {showDelete ? (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      variant="destructive"
                      onSelect={() => setDeleteOpen(true)}
                    >
                      <Trash2Icon />
                      {t.agents.delete}
                    </DropdownMenuItem>
                  </>
                ) : null}
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </article>

      <Sheet open={profileOpen} onOpenChange={setProfileOpen}>
        <SheetContent className="w-full gap-0 sm:max-w-md">
          <SheetHeader>
            <SheetTitle>{t.agents.profileTitle}</SheetTitle>
            <SheetDescription className="sr-only">
              {t.agents.profileDescription}
            </SheetDescription>
          </SheetHeader>
          <div className="app-scroll-region flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-4 pb-4">
            <div className="flex items-center gap-4">
              <DigitalEmployeeAvatar agent={agent} className="size-24" />
              <div className="min-w-0">
                <h2 className="text-lg font-semibold break-words">
                  {displayName}
                </h2>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge variant="outline">{sourceLabel}</Badge>
                  {categoryLabel ? (
                    <Badge variant="secondary">{categoryLabel}</Badge>
                  ) : null}
                </div>
              </div>
            </div>
            {agent.description ? (
              <p className="text-muted-foreground text-sm leading-7">
                {agent.description}
              </p>
            ) : null}
            <dl className="text-sm" data-testid="employee-creator">
              <dt className="text-muted-foreground">
                {t.employeeDiscovery.creator}
              </dt>
              <dd className="mt-1 break-words">
                {agent.creator_name ?? t.employeeDiscovery.creatorUnknown}
              </dd>
            </dl>
            {capabilityLabels.length > 0 ? <Separator /> : null}
            {capabilityLabels.length > 0 ? (
              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">{t.agents.capabilities}</h3>
                <div className="flex flex-wrap gap-2">
                  {capabilityLabels.map((label) => (
                    <Badge key={label} variant="secondary">
                      {label}
                    </Badge>
                  ))}
                </div>
              </section>
            ) : null}
          </div>
          <SheetFooter>{primaryAction}</SheetFooter>
        </SheetContent>
      </Sheet>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.agents.delete}</DialogTitle>
            <DialogDescription>{t.agents.deleteConfirm}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
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
            <Button variant="outline" onClick={() => setUnpublishOpen(false)}>
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
