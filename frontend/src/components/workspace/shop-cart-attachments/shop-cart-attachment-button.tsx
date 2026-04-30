"use client";

import { ShoppingCartIcon } from "lucide-react";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import {
  PromptInputButton,
  usePromptInputAttachments,
} from "@/components/ai-elements/prompt-input";
import { useI18n } from "@/core/i18n/hooks";
import { createMockShopCartFiles } from "@/core/shop-cart/mock-files";
import { cn } from "@/lib/utils";

import { Tooltip } from "../tooltip";

import { ShopCartDialog } from "./shop-cart-dialog";

export function ShopCartAttachmentButton({ className }: { className?: string }) {
  const { t } = useI18n();
  const attachments = usePromptInputAttachments();
  const [open, setOpen] = useState(false);

  const handleSelectedIds = useCallback(
    (ids: string[]) => {
      if (ids.length === 0) {
        toast.error(t.inputBox.shopCartEmptySelection);
        return false;
      }

      const { files, unknownIds } = createMockShopCartFiles(ids);
      if (unknownIds.length > 0) {
        toast.warning(t.inputBox.shopCartUnknownFiles);
      }
      if (files.length === 0) {
        toast.error(t.inputBox.shopCartNoKnownFiles);
        return false;
      }

      attachments.add(files);
      toast.success(t.inputBox.shopCartAddedFiles(files.length));
      return true;
    },
    [attachments, t],
  );

  return (
    <>
      <Tooltip content={t.inputBox.addShopCartAttachments}>
        <PromptInputButton
          className={cn("gap-1 px-2!", className)}
          onClick={() => setOpen(true)}
        >
          <ShoppingCartIcon className="size-3" />
          <span className="text-xs font-normal">{t.inputBox.shopCart}</span>
        </PromptInputButton>
      </Tooltip>
      <ShopCartDialog
        open={open}
        onOpenChange={setOpen}
        onSelectedIds={handleSelectedIds}
      />
    </>
  );
}
