"use client";

import { DatabaseIcon, DatabaseZapIcon, ListIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  PromptInputButton,
  usePromptInputAttachments,
} from "@/components/ai-elements/prompt-input";
import { useI18n } from "@/core/i18n/hooks";
import type {
  DownloadableNewShopCartFile,
  NewShopCartFile,
} from "@/core/shop-cart/api-files";
import {
  downloadNewShopCartFile,
  fetchNewShopCartFiles,
} from "@/core/shop-cart/new-api";
import type { AgentThreadContext } from "@/core/threads";
import { cn } from "@/lib/utils";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../ui/dropdown-menu";
import { Tooltip } from "../tooltip";

import { NewShopCartDialog } from "./new-shop-cart-dialog";

type ShopCartInputContext = Omit<
  AgentThreadContext,
  "thread_id" | "is_plan_mode" | "thinking_enabled" | "subagent_enabled"
> & {
  mode: "flash" | "thinking" | "pro" | "ultra" | undefined;
  reasoning_effort?: "minimal" | "low" | "medium" | "high";
};

export function ShopCartAttachmentButton({
  className,
  context,
  disabled = false,
  onContextChange,
}: {
  className?: string;
  context: ShopCartInputContext;
  disabled?: boolean;
  onContextChange?: (context: ShopCartInputContext) => void;
}) {
  const { t } = useI18n();
  const attachments = usePromptInputAttachments();
  const [newShopCartOpen, setNewShopCartOpen] = useState(false);
  const [newShopCartFiles, setNewShopCartFiles] = useState<NewShopCartFile[]>(
    [],
  );
  const [newShopCartLoading, setNewShopCartLoading] = useState(false);
  const requestSequence = useRef(0);
  const dataLakeEnabled = context.datalake_enabled !== false;

  useEffect(() => {
    if (dataLakeEnabled) {
      return;
    }
    requestSequence.current += 1;
    setNewShopCartOpen(false);
    setNewShopCartLoading(false);
    setNewShopCartFiles([]);
  }, [dataLakeEnabled]);

  const handleNewSelectedFiles = useCallback(
    async (selectedFiles: DownloadableNewShopCartFile[]) => {
      if (selectedFiles.length === 0) {
        toast.error(t.inputBox.shopCartEmptySelection);
        return false;
      }

      const toastId = toast.loading(t.inputBox.shopCartDownloading);
      const results = await Promise.all(
        selectedFiles.map(downloadNewShopCartFile),
      );
      toast.dismiss(toastId);

      const files = results
        .filter((result): result is { ok: true; file: File } => result.ok)
        .map((result) => result.file);
      const failed = results.length - files.length;

      if (files.length === 0) {
        toast.error(t.inputBox.shopCartDownloadFailed);
        return false;
      }

      attachments.add(files);
      if (files.length > 0) {
        toast.success(t.inputBox.shopCartAddedFiles(files.length));
      }
      if (failed > 0) {
        toast.warning(t.inputBox.shopCartPartialDownloadFailed(failed));
      }
      return true;
    },
    [attachments, t],
  );

  const loadNewShopCartFiles = useCallback(async () => {
    if (!dataLakeEnabled) {
      return;
    }
    const sequence = ++requestSequence.current;
    setNewShopCartLoading(true);
    const result = await fetchNewShopCartFiles();
    if (sequence !== requestSequence.current || !dataLakeEnabled) {
      return;
    }
    setNewShopCartLoading(false);

    if (!result.ok) {
      toast.error(t.inputBox.newShopCartLoadFailed);
      return;
    }

    setNewShopCartFiles(result.files);
  }, [dataLakeEnabled, t.inputBox.newShopCartLoadFailed]);

  const openShopCartWindow = useCallback(() => {
    if (!dataLakeEnabled) {
      return;
    }
    setNewShopCartOpen(true);
    void loadNewShopCartFiles();
  }, [dataLakeEnabled, loadNewShopCartFiles]);

  const updateDataLakeEnabled = useCallback(
    (enabled: boolean) => {
      if (!enabled) {
        requestSequence.current += 1;
        setNewShopCartOpen(false);
        setNewShopCartLoading(false);
        setNewShopCartFiles([]);
      }
      onContextChange?.({ ...context, datalake_enabled: enabled });
    },
    [context, onContextChange],
  );

  return (
    <>
      <DropdownMenu>
        <Tooltip content={t.inputBox.addShopCartAttachments}>
          <DropdownMenuTrigger asChild>
            <PromptInputButton
              className={cn(
                "gap-1 px-2!",
                dataLakeEnabled &&
                  "border-[#6d5dfc]/40 bg-[#f4f2ff]",
                className,
              )}
              disabled={disabled}
            >
              <DatabaseIcon className="size-3" />
              <span className="text-xs font-normal">{t.inputBox.shopCart}</span>
            </PromptInputButton>
          </DropdownMenuTrigger>
        </Tooltip>
        <DropdownMenuContent align="start">
          {dataLakeEnabled ? (
            <>
              <DropdownMenuItem onSelect={openShopCartWindow}>
                <ListIcon />
                {t.inputBox.dataLakeFileList}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => updateDataLakeEnabled(false)}>
                <DatabaseZapIcon />
                {t.inputBox.disableDataLake}
              </DropdownMenuItem>
            </>
          ) : (
            <DropdownMenuItem onSelect={() => updateDataLakeEnabled(true)}>
              <DatabaseIcon />
              {t.inputBox.enableDataLake}
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      {dataLakeEnabled ? (
        <NewShopCartDialog
          files={newShopCartFiles}
          loading={newShopCartLoading}
          open={newShopCartOpen}
          onOpenChange={setNewShopCartOpen}
          onRefresh={loadNewShopCartFiles}
          onSelectedFiles={handleNewSelectedFiles}
        />
      ) : null}
    </>
  );
}
