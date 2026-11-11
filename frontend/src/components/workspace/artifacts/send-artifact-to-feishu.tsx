"use client";

import { LoaderIcon, SendIcon } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { isFeishuSendableArtifact } from "@/core/artifacts/utils";
import { useChannelConnections } from "@/core/channels/hooks";
import {
  feishuSendTargets,
  personalFeishuTarget,
} from "@/core/channels/targets";
import { feishuExportCopy } from "@/core/i18n/feishu-export";
import { useI18n } from "@/core/i18n/hooks";
import { sendThreadArtifactsToFeishu } from "@/core/threads/export";

/**
 * One-click "send this generated file to my 海能 Work chat" button.
 *
 * Sits beside Preview / Download on an artifact card and in the file detail
 * header, so a user who just generated a report can push the real file to
 * their phone without asking the bot again. The gateway re-checks every path
 * (published by this thread, still inside `outputs/`, under 20MB); this
 * component only mirrors the path rule, plus the binding gate: without a
 * verified personal chat there is nowhere to send, so it stays disabled.
 */
export function SendArtifactToFeishuButton({
  threadId,
  filepaths,
  variant = "ghost",
  size = "sm",
  showLabel = true,
  className,
}: {
  threadId: string;
  filepaths: string[];
  variant?: "ghost" | "outline" | "secondary" | "default";
  size?: "sm" | "icon-sm" | "default";
  showLabel?: boolean;
  className?: string;
}) {
  const { locale } = useI18n();
  const copy = useMemo(() => feishuExportCopy(locale), [locale]);
  const { connections } = useChannelConnections();
  const [isSending, setIsSending] = useState(false);

  const target = useMemo(
    () => personalFeishuTarget(feishuSendTargets(connections)),
    [connections],
  );
  const sendablePaths = useMemo(
    () => filepaths.filter(isFeishuSendableArtifact),
    [filepaths],
  );
  const isDisabled = !target || sendablePaths.length === 0 || isSending;

  const handleClick = useCallback(
    async (event: React.MouseEvent) => {
      // The card itself opens the artifact panel; sending must not do that.
      event.stopPropagation();
      event.preventDefault();
      if (isDisabled) {
        return;
      }

      setIsSending(true);
      try {
        const result = await sendThreadArtifactsToFeishu(
          threadId,
          sendablePaths,
        );
        const namedTarget = result.target_name?.trim() ?? "";
        const destination =
          namedTarget.length > 0 ? namedTarget : copy.privateChat;
        toast.success(`${copy.sendFileSuccess} · ${destination}`);
        if (result.skipped.length > 0) {
          toast.warning(`${copy.skipped}: ${result.skipped.join(", ")}`);
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : String(error));
      } finally {
        setIsSending(false);
      }
    },
    [copy, isDisabled, sendablePaths, threadId],
  );

  const label = isSending ? copy.sending : copy.sendFile;

  return (
    <Button
      type="button"
      variant={variant}
      size={size}
      className={className}
      disabled={isDisabled}
      title={target ? copy.sendFileTooltip : copy.bindFirst}
      aria-label={label}
      data-testid="send-artifact-to-feishu"
      onClick={handleClick}
    >
      {isSending ? (
        <LoaderIcon className="size-4 animate-spin" />
      ) : (
        <SendIcon className="size-4" />
      )}
      {showLabel ? (
        <span className="cnooc-artifact-action-label">{label}</span>
      ) : null}
    </Button>
  );
}
