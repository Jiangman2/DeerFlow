"use client";

import {
  Download,
  FileJson,
  FileText,
  Paperclip,
  Send,
  Users,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { filterUserVisibleArtifacts } from "@/core/artifacts/utils";
import { useChannelConnections } from "@/core/channels/hooks";
import {
  feishuSendTargets,
  type FeishuSendTarget,
} from "@/core/channels/targets";
import { feishuExportCopy } from "@/core/i18n/feishu-export";
import { useI18n } from "@/core/i18n/hooks";
import {
  exportThreadAsJSON,
  exportThreadAsMarkdown,
  sendThreadArtifactsToFeishu,
  sendThreadAsMarkdownToFeishu,
} from "@/core/threads/export";
import type { AgentThread } from "@/core/threads/types";

import { useThread } from "./messages/context";
import { Tooltip } from "./tooltip";

/** Only files this thread published under `outputs/` can leave the sandbox. */
const SENDABLE_ARTIFACT_PREFIX = "/mnt/user-data/outputs/";

export function ExportTrigger({
  threadId,
  inMenu = false,
}: {
  threadId: string;
  inMenu?: boolean;
}) {
  const { t, locale } = useI18n();
  const copy = useMemo(() => feishuExportCopy(locale), [locale]);
  const { thread } = useThread();
  const { connections } = useChannelConnections();
  const [sendingToFeishu, setSendingToFeishu] = useState(false);

  const messages = thread.messages;

  const targets = feishuSendTargets(connections);
  const groupTargets = targets.filter((target) => !target.isPersonal);
  const artifacts = filterUserVisibleArtifacts(
    thread.values.artifacts ?? [],
  ).filter((path) => path.startsWith(SENDABLE_ARTIFACT_PREFIX));

  /** Name the destination in the toast so the user always sees where it went. */
  const describeTarget = useCallback(
    (result: { target_name: string | null; target_chat_type: string }) => {
      const name = result.target_name?.trim();
      if (name) {
        return name;
      }
      return result.target_chat_type === "p2p"
        ? copy.privateChat
        : copy.groupChat;
    },
    [copy],
  );

  const handleExport = useCallback(
    (format: "markdown" | "json") => {
      if (messages.length === 0) {
        toast.error(t.conversation.noMessages);
        return;
      }
      const agentThread = {
        thread_id: threadId,
        updated_at: new Date().toISOString(),
        values: thread.values,
      } as AgentThread;

      if (format === "markdown") {
        exportThreadAsMarkdown(agentThread, messages);
      } else {
        exportThreadAsJSON(agentThread, messages);
      }
      toast.success(t.common.exportSuccess);
    },
    [messages, thread.values, threadId, t],
  );

  const runSend = useCallback(async (send: () => Promise<void>) => {
    setSendingToFeishu(true);
    try {
      await send();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSendingToFeishu(false);
    }
  }, []);

  const handleSendToFeishu = useCallback(
    (target?: FeishuSendTarget) =>
      runSend(async () => {
        if (messages.length === 0) {
          toast.error(t.conversation.noMessages);
          return;
        }
        const agentThread = {
          thread_id: threadId,
          updated_at: new Date().toISOString(),
          values: thread.values,
        } as AgentThread;

        const result = await sendThreadAsMarkdownToFeishu(
          agentThread,
          messages,
          target?.chatId,
        );
        toast.success(`${copy.toFeishuSuccess} · ${describeTarget(result)}`);
      }),
    [copy, describeTarget, messages, runSend, t, thread.values, threadId],
  );

  const handleSendFilesToFeishu = useCallback(
    (target?: FeishuSendTarget) =>
      runSend(async () => {
        if (artifacts.length === 0) {
          toast.error(copy.noArtifacts);
          return;
        }
        const result = await sendThreadArtifactsToFeishu(
          threadId,
          artifacts,
          target?.chatId,
        );
        toast.success(`${copy.filesSuccess} · ${describeTarget(result)}`);
        if (result.skipped.length > 0) {
          toast.warning(`${copy.skipped}: ${result.skipped.join(", ")}`);
        }
      }),
    [artifacts, copy, describeTarget, runSend, threadId],
  );

  if (messages.length === 0) {
    return null;
  }

  const formats = (
    <DropdownMenuGroup>
      <DropdownMenuItem onSelect={() => handleExport("markdown")}>
        <FileText className="text-muted-foreground" />
        <span>{t.common.exportAsMarkdown}</span>
      </DropdownMenuItem>
      <DropdownMenuItem onSelect={() => handleExport("json")}>
        <FileJson className="text-muted-foreground" />
        <span>{t.common.exportAsJSON}</span>
      </DropdownMenuItem>
      <DropdownMenuItem
        disabled={sendingToFeishu}
        onSelect={() => void handleSendToFeishu()}
      >
        <Send className="text-muted-foreground" />
        <span>{copy.toFeishu}</span>
      </DropdownMenuItem>
      {artifacts.length > 0 ? (
        <DropdownMenuItem
          disabled={sendingToFeishu}
          onSelect={() => void handleSendFilesToFeishu()}
        >
          <Paperclip className="text-muted-foreground" />
          <span>{copy.files}</span>
        </DropdownMenuItem>
      ) : null}
      {/* Group chats are never a default: the user names the group and sees its
          name before the conversation or its files leave the browser. */}
      {groupTargets.length > 0 ? (
        <DropdownMenuSub>
          <DropdownMenuSubTrigger disabled={sendingToFeishu}>
            <Users className="text-muted-foreground" />
            <span>{copy.toGroup}</span>
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            {groupTargets.map((target) => (
              <DropdownMenuGroup key={target.chatId}>
                <DropdownMenuItem
                  disabled={sendingToFeishu}
                  onSelect={() => void handleSendToFeishu(target)}
                >
                  <Send className="text-muted-foreground" />
                  <span>{target.label}</span>
                </DropdownMenuItem>
                {artifacts.length > 0 ? (
                  <DropdownMenuItem
                    disabled={sendingToFeishu}
                    onSelect={() => void handleSendFilesToFeishu(target)}
                  >
                    <Paperclip className="text-muted-foreground" />
                    <span>
                      {copy.files} · {target.label}
                    </span>
                  </DropdownMenuItem>
                ) : null}
              </DropdownMenuGroup>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      ) : null}
    </DropdownMenuGroup>
  );
  if (inMenu) {
    return (
      <DropdownMenuSub>
        <DropdownMenuSubTrigger>
          <Download />
          {t.common.export}
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent>{formats}</DropdownMenuSubContent>
      </DropdownMenuSub>
    );
  }

  return (
    <DropdownMenu>
      <Tooltip content={t.common.export}>
        <DropdownMenuTrigger asChild>
          <Button
            aria-label={t.common.export}
            className="text-muted-foreground hover:text-foreground size-8 px-0 sm:h-9 sm:w-auto sm:px-4"
            variant="ghost"
          >
            <Download />
            <span className="hidden sm:inline">{t.common.export}</span>
          </Button>
        </DropdownMenuTrigger>
      </Tooltip>
      <DropdownMenuContent align="end">{formats}</DropdownMenuContent>
    </DropdownMenu>
  );
}
