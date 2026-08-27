"use client";

import { DatabaseIcon, DatabaseZapIcon, ListIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { PromptInputButton } from "@/components/ai-elements/prompt-input";
import { ensureDataLakeAuthorization } from "@/core/data-lake/auth";
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
  onAddFiles,
  onContextChange,
}: {
  className?: string;
  context: ShopCartInputContext;
  disabled?: boolean;
  onAddFiles: (files: File[]) => number;
  onContextChange?: (context: ShopCartInputContext) => void;
}) {
  const { t } = useI18n();
  const [newShopCartOpen, setNewShopCartOpen] = useState(false);
  const [newShopCartFiles, setNewShopCartFiles] = useState<NewShopCartFile[]>(
    [],
  );
  const [newShopCartLoading, setNewShopCartLoading] = useState(false);
  const [authorizing, setAuthorizing] = useState(false);
  const requestSequence = useRef(0);
  const onAddFilesRef = useRef(onAddFiles);
  const onContextChangeRef = useRef(onContextChange);
  const contextRef = useRef(context);
  onAddFilesRef.current = onAddFiles;
  onContextChangeRef.current = onContextChange;
  contextRef.current = context;
  const dataLakeEnabled = context.datalake_enabled === true;

  const resetDataLakeUi = useCallback(() => {
    requestSequence.current += 1;
    setNewShopCartOpen(false);
    setNewShopCartLoading(false);
    setNewShopCartFiles([]);
  }, []);

  const commitDataLakeEnabled = useCallback((enabled: boolean) => {
    const nextContext = {
      ...contextRef.current,
      datalake_enabled: enabled,
    };
    contextRef.current = nextContext;
    onContextChangeRef.current?.(nextContext);
  }, []);

  useEffect(() => {
    if (dataLakeEnabled) {
      return;
    }
    resetDataLakeUi();
  }, [dataLakeEnabled, resetDataLakeUi]);

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

      if (results.some((result) => !result.ok && result.status === 401)) {
        resetDataLakeUi();
        commitDataLakeEnabled(false);
      }

      if (files.length === 0) {
        toast.error(t.inputBox.shopCartDownloadFailed);
        return false;
      }

      const added = onAddFilesRef.current(files);
      if (added > 0) {
        toast.success(t.inputBox.shopCartAddedFiles(added));
      }
      if (failed > 0) {
        toast.warning(t.inputBox.shopCartPartialDownloadFailed(failed));
      }
      return added > 0;
    },
    [commitDataLakeEnabled, resetDataLakeUi, t],
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
      if (result.status === 401) {
        resetDataLakeUi();
        commitDataLakeEnabled(false);
      }
      toast.error(t.inputBox.newShopCartLoadFailed);
      return;
    }

    setNewShopCartFiles(result.files);
  }, [
    commitDataLakeEnabled,
    dataLakeEnabled,
    resetDataLakeUi,
    t.inputBox.newShopCartLoadFailed,
  ]);

  const openShopCartWindow = useCallback(async () => {
    if (!dataLakeEnabled || authorizing) {
      return;
    }

    setAuthorizing(true);
    try {
      const authorized = await ensureDataLakeAuthorization();
      if (!authorized.ok) {
        if (authorized.status === 401 || authorized.status === 403) {
          resetDataLakeUi();
          commitDataLakeEnabled(false);
        }
        toast.error(
          authorized.message ?? t.inputBox.dataLakeAuthorizationFailed,
        );
        return;
      }

      setNewShopCartOpen(true);
      await loadNewShopCartFiles();
    } finally {
      setAuthorizing(false);
    }
  }, [
    authorizing,
    commitDataLakeEnabled,
    dataLakeEnabled,
    loadNewShopCartFiles,
    resetDataLakeUi,
    t.inputBox.dataLakeAuthorizationFailed,
  ]);

  const updateDataLakeEnabled = useCallback(
    async (enabled: boolean) => {
      if (!enabled) {
        resetDataLakeUi();
        commitDataLakeEnabled(false);
        return;
      }

      setAuthorizing(true);
      const authorized = await ensureDataLakeAuthorization();
      setAuthorizing(false);
      if (!authorized.ok) {
        toast.error(
          authorized.message ?? t.inputBox.dataLakeAuthorizationFailed,
        );
        return;
      }
      commitDataLakeEnabled(true);
    },
    [
      commitDataLakeEnabled,
      resetDataLakeUi,
      t.inputBox.dataLakeAuthorizationFailed,
    ],
  );

  return (
    <>
      <DropdownMenu>
        <Tooltip content={t.inputBox.addShopCartAttachments}>
          <DropdownMenuTrigger asChild>
            <PromptInputButton
              className={cn(
                "gap-1 px-2!",
                dataLakeEnabled && "border-[#6d5dfc]/40 bg-[#f4f2ff]",
                className,
              )}
              data-active={dataLakeEnabled}
              disabled={disabled || authorizing}
            >
              <DatabaseIcon className="size-3" />
              <span className="text-xs font-normal">{t.inputBox.shopCart}</span>
            </PromptInputButton>
          </DropdownMenuTrigger>
        </Tooltip>
        <DropdownMenuContent align="start">
          {dataLakeEnabled ? (
            <>
              <DropdownMenuItem
                disabled={authorizing}
                onSelect={() => void openShopCartWindow()}
              >
                <ListIcon />
                {t.inputBox.dataLakeFileList}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onSelect={() => void updateDataLakeEnabled(false)}
              >
                <DatabaseZapIcon />
                {t.inputBox.disableDataLake}
              </DropdownMenuItem>
            </>
          ) : (
            <DropdownMenuItem
              disabled={authorizing}
              onSelect={() => void updateDataLakeEnabled(true)}
            >
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
