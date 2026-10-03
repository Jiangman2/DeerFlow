"use client";

import { ExternalLinkIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/core/i18n/hooks";
import type { getShopCartFrameConfig } from "@/core/shop-cart/config";
import {
  parseShopCartSelectedFiles,
  type ShopCartSelectedFile,
} from "@/core/shop-cart/selected-files";

type ShopCartDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onReopenShopCart: () => void;
  onSelectedFiles: (
    files: ShopCartSelectedFile[],
    isMockMode: boolean,
  ) => boolean | void | Promise<boolean | void>;
  shopCartConfig: ReturnType<typeof getShopCartFrameConfig> | null;
  shopCartWindow: Window | null;
};

const REQUEST_TIMEOUT_MS = 2500;

export function ShopCartDialog({
  open,
  onOpenChange,
  onReopenShopCart,
  onSelectedFiles,
  shopCartConfig,
  shopCartWindow,
}: ShopCartDialogProps) {
  const { t } = useI18n();
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [requesting, setRequesting] = useState(false);

  const clearPendingRequest = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    setRequesting(false);
  }, []);

  useEffect(() => {
    if (!open) {
      clearPendingRequest();
    }
  }, [clearPendingRequest, open]);

  useEffect(() => {
    return () => clearPendingRequest();
  }, [clearPendingRequest]);

  useEffect(() => {
    if (!open) {
      return;
    }

    if (!shopCartConfig) {
      return;
    }

    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== shopCartConfig.allowedOrigin) {
        return;
      }
      if (shopCartWindow && event.source !== shopCartWindow) {
        return;
      }
      if (event.data?.type !== "shopCartSelectedIds") {
        return;
      }

      clearPendingRequest();
      const result = parseShopCartSelectedFiles(event.data.data);
      if (!result.ok) {
        toast.error(t.inputBox.shopCartInvalidPayload);
        return;
      }

      void Promise.resolve(
        onSelectedFiles(result.files, shopCartConfig.isMockMode),
      ).then((shouldClose) => {
        if (shouldClose !== false) {
          onOpenChange(false);
        }
      });
    };

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [
    clearPendingRequest,
    onOpenChange,
    onSelectedFiles,
    open,
    shopCartConfig,
    shopCartWindow,
    t.inputBox.shopCartInvalidPayload,
  ]);

  const requestSelectedIds = useCallback(() => {
    if (!shopCartWindow || shopCartWindow.closed) {
      toast.error(t.inputBox.shopCartNoResponse);
      return;
    }

    if (!shopCartConfig) {
      toast.error(t.inputBox.shopCartNoResponse);
      return;
    }

    setRequesting(true);
    shopCartWindow.postMessage(
      {
        type: "getShopCartSelectedIds",
      },
      shopCartConfig.allowedOrigin,
    );

    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      setRequesting(false);
      toast.error(t.inputBox.shopCartNoResponse);
    }, REQUEST_TIMEOUT_MS);
  }, [shopCartConfig, shopCartWindow, t.inputBox.shopCartNoResponse]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[min(520px,calc(100vw-2rem))] gap-4 p-0">
        <DialogHeader className="px-5 pt-5">
          <DialogTitle>{t.inputBox.shopCartDialogTitle}</DialogTitle>
          <DialogDescription>
            {t.inputBox.shopCartDialogDescription}
          </DialogDescription>
        </DialogHeader>

        <div className="text-muted-foreground px-5 text-sm">
          {t.inputBox.shopCartWindowDescription}
        </div>

        <DialogFooter className="border-t px-5 py-4">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            {t.common.cancel}
          </Button>
          <Button type="button" variant="outline" onClick={onReopenShopCart}>
            <ExternalLinkIcon className="size-4" />
            {t.inputBox.shopCartReopen}
          </Button>
          <Button
            disabled={requesting}
            type="button"
            onClick={requestSelectedIds}
          >
            {requesting
              ? t.inputBox.shopCartAdding
              : t.inputBox.shopCartAddSelected}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
