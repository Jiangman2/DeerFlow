"use client";

import {
  BotIcon,
  ExternalLinkIcon,
  EyeIcon,
  MessageSquareIcon,
  MoreHorizontalIcon,
  PencilIcon,
  Share2Icon,
  Trash2Icon,
  Undo2Icon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
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
  getDefaultExpertAvatar,
  getExpertAvatarById,
  getStoredExpertAvatarId,
  recordExternalAgentClick,
  resolveExternalAgentLaunchHref,
  useDeleteAgent,
  usePublishAgent,
  useUnpublishTeamAgent,
} from "@/core/agents";
import type { Agent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface AgentCardProps {
  agent: Agent;
  categoryLabel?: string;
  showDelete?: boolean;
  showPublish?: boolean;
  showUnpublish?: boolean;
  onEdit?: (agent: Agent) => void;
}

function DigitalEmployeeAvatar({
  agent,
  className,
}: {
  agent: Agent;
  className?: string;
}) {
  const [storedAvatarId, setStoredAvatarId] = useState<string>();
  useEffect(() => {
    setStoredAvatarId(getStoredExpertAvatarId(agent.name));
  }, [agent.name]);
  const preset =
    getExpertAvatarById(agent.avatar_id ?? storedAvatarId) ??
    getDefaultExpertAvatar(agent);
  const accessibleName = agent.display_name?.trim();

  return (
    <Avatar
      className={cn(
        "bg-primary/5 ring-background shrink-0 rounded-full border-2 border-white shadow-sm ring-1 ring-blue-100 dark:border-slate-800 dark:ring-slate-700",
        className,
      )}
    >
      <AvatarImage
        src={agent.avatar_url ?? preset.src}
        alt={accessibleName?.length ? accessibleName : agent.name}
        className="rounded-full object-cover"
      />
      <AvatarFallback className="rounded-full bg-blue-50 text-blue-700">
        <BotIcon aria-hidden="true" />
      </AvatarFallback>
    </Avatar>
  );
}

export function AgentCard({
  agent,
  categoryLabel,
  showDelete = true,
  showPublish = false,
  showUnpublish = false,
  onEdit,
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
    <Button asChild size="sm" className="h-8 shrink-0 rounded-lg px-3">
      <a
        href={externalHref}
        target="_blank"
        rel="noreferrer"
        onClick={recordExternalClick}
      >
        <ExternalLinkIcon data-icon="inline-start" />
        {t.agents.openExternal}
      </a>
    </Button>
  ) : (
    <Button
      size="sm"
      className="h-8 shrink-0 rounded-lg px-3"
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
        <div className="flex min-w-0 flex-1 items-start gap-3 sm:items-center">
          <DigitalEmployeeAvatar agent={agent} className="size-14 sm:size-16" />
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 flex-wrap items-center gap-1.5">
              <h2 className="max-w-full truncate text-sm font-semibold tracking-tight sm:text-[15px]">
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
              {categoryLabel ? (
                <span className="text-muted-foreground text-[11px]">
                  · {categoryLabel}
                </span>
              ) : null}
            </div>
            {agent.display_name && agent.display_name !== agent.name ? (
              <p className="text-muted-foreground mt-0.5 truncate text-[11px]">
                {agent.name}
              </p>
            ) : null}
            <p className="text-muted-foreground mt-1 line-clamp-1 text-xs leading-5 sm:text-[13px]">
              {agent.description || t.agents.noDescription}
            </p>
            {capabilityLabels.length > 0 ? (
              <div className="mt-1.5 flex min-w-0 gap-1 overflow-hidden">
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

        <div className="flex shrink-0 items-center justify-end gap-1.5 pl-[4.25rem] sm:pl-0">
          {primaryAction}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t.agents.more}
                className="rounded-lg"
              >
                <MoreHorizontalIcon />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44">
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
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </article>

      <Sheet open={profileOpen} onOpenChange={setProfileOpen}>
        <SheetContent className="w-full gap-0 sm:max-w-md">
          <SheetHeader>
            <SheetTitle>{t.agents.profileTitle}</SheetTitle>
            <SheetDescription>{t.agents.profileDescription}</SheetDescription>
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
