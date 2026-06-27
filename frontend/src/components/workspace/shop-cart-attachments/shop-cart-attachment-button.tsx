"use client";

import { ShoppingCartIcon } from "lucide-react";
import { useCallback, useState } from "react";
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
import { getShopCartFrameConfig, getShopCartMode } from "@/core/shop-cart/config";
import { downloadShopCartFile } from "@/core/shop-cart/download";
import { createMockShopCartFiles } from "@/core/shop-cart/mock-files";
import {
  downloadNewShopCartFile,
  fetchNewShopCartFiles,
} from "@/core/shop-cart/new-api";
import type { ShopCartSelectedFile } from "@/core/shop-cart/selected-files";
import { cn } from "@/lib/utils";

import { Tooltip } from "../tooltip";

import { NewShopCartDialog } from "./new-shop-cart-dialog";
import { ShopCartDialog } from "./shop-cart-dialog";

export function ShopCartAttachmentButton({ className }: { className?: string }) {
  const { t } = useI18n();
  const attachments = usePromptInputAttachments();
  const [open, setOpen] = useState(false);
  const [newShopCartOpen, setNewShopCartOpen] = useState(false);
  const [newShopCartFiles, setNewShopCartFiles] = useState<NewShopCartFile[]>(
    [],
  );
  const [newShopCartLoading, setNewShopCartLoading] = useState(false);
  const [shopCartWindow, setShopCartWindow] = useState<Window | null>(null);
  const [shopCartConfig, setShopCartConfig] = useState<ReturnType<
    typeof getShopCartFrameConfig
  > | null>(null);
  const shopCartMode = getShopCartMode();

  const handleSelectedFiles = useCallback(
    async (selectedFiles: ShopCartSelectedFile[], isMockMode: boolean) => {
      if (selectedFiles.length === 0) {
        toast.error(t.inputBox.shopCartEmptySelection);
        return false;
      }

      if (isMockMode) {
        const { files, unknownIds, failedIds } = await createMockShopCartFiles(
          selectedFiles.map((file) => file.fileId),
        );
        if (unknownIds.length > 0) {
          toast.warning(t.inputBox.shopCartUnknownFiles);
        }
        if (failedIds.length > 0) {
          toast.warning(t.inputBox.shopCartPartialDownloadFailed(failedIds.length));
        }
        if (files.length === 0) {
          toast.error(t.inputBox.shopCartNoKnownFiles);
          return false;
        }

        attachments.add(files);
        if (files.length > 0) {
          toast.success(t.inputBox.shopCartAddedFiles(files.length));
        }
        return true;
      }

      const toastId = toast.loading(t.inputBox.shopCartDownloading);
      const results = await Promise.all(selectedFiles.map(downloadShopCartFile));
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
    setNewShopCartLoading(true);
    const result = await fetchNewShopCartFiles();
    setNewShopCartLoading(false);

    if (!result.ok) {
      toast.error(t.inputBox.newShopCartLoadFailed);
      return;
    }

    setNewShopCartFiles(result.files);
  }, [t.inputBox.newShopCartLoadFailed]);

  const openShopCartWindow = useCallback(() => {
    if (shopCartMode === "api") {
      setNewShopCartOpen(true);
      void loadNewShopCartFiles();
      return;
    }

    const config = getShopCartFrameConfig(window.location.origin);
    const openedWindow = window.open(
      config.iframeUrl,
      "deerflow-shop-cart",
      "popup=yes,width=1200,height=800",
    );

    if (!openedWindow) {
      toast.error(t.inputBox.shopCartPopupBlocked);
      return;
    }

    openedWindow.focus();
    setShopCartConfig(config);
    setShopCartWindow(openedWindow);
    setOpen(true);
  }, [loadNewShopCartFiles, shopCartMode, t.inputBox.shopCartPopupBlocked]);

  return (
    <>
      <Tooltip content={t.inputBox.addShopCartAttachments}>
        <PromptInputButton
          className={cn("gap-1 px-2!", className)}
          onClick={openShopCartWindow}
        >
          <ShoppingCartIcon className="size-3" />
          <span className="text-xs font-normal">{t.inputBox.shopCart}</span>
        </PromptInputButton>
      </Tooltip>
      <ShopCartDialog
        shopCartConfig={shopCartConfig}
        shopCartWindow={shopCartWindow}
        open={open}
        onOpenChange={setOpen}
        onReopenShopCart={openShopCartWindow}
        onSelectedFiles={handleSelectedFiles}
      />
      <NewShopCartDialog
        files={newShopCartFiles}
        loading={newShopCartLoading}
        open={newShopCartOpen}
        onOpenChange={setNewShopCartOpen}
        onRefresh={loadNewShopCartFiles}
        onSelectedFiles={handleNewSelectedFiles}
      />
    </>
  );
}
