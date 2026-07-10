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
import {
  downloadNewShopCartFile,
  fetchNewShopCartFiles,
} from "@/core/shop-cart/new-api";
import { cn } from "@/lib/utils";

import { Tooltip } from "../tooltip";

import { NewShopCartDialog } from "./new-shop-cart-dialog";

export function ShopCartAttachmentButton({
  className,
}: {
  className?: string;
}) {
  const { t } = useI18n();
  const attachments = usePromptInputAttachments();
  const [newShopCartOpen, setNewShopCartOpen] = useState(false);
  const [newShopCartFiles, setNewShopCartFiles] = useState<NewShopCartFile[]>(
    [],
  );
  const [newShopCartLoading, setNewShopCartLoading] = useState(false);

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
    setNewShopCartOpen(true);
    void loadNewShopCartFiles();
  }, [loadNewShopCartFiles]);

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
