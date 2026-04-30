"use client";

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

type ShopCartDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelectedIds: (ids: string[]) => boolean | void;
};

const SHOP_CART_IFRAME_SRC = "/mock/shop-cart-iframe";
const REQUEST_TIMEOUT_MS = 2500;

export function ShopCartDialog({
  open,
  onOpenChange,
  onSelectedIds,
}: ShopCartDialogProps) {
  const { t } = useI18n();
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
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

    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) {
        return;
      }
      if (event.source !== iframeRef.current?.contentWindow) {
        return;
      }
      if (event.data?.type !== "shopCartSelectedIds") {
        return;
      }

      clearPendingRequest();
      const ids = Array.isArray(event.data.data)
        ? event.data.data.filter((id: unknown): id is string => typeof id === "string")
        : [];
      const shouldClose = onSelectedIds(ids);
      if (shouldClose !== false) {
        onOpenChange(false);
      }
    };

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [clearPendingRequest, onOpenChange, onSelectedIds, open]);

  const requestSelectedIds = useCallback(() => {
    const targetWindow = iframeRef.current?.contentWindow;
    if (!targetWindow) {
      toast.error(t.inputBox.shopCartNoResponse);
      return;
    }

    setRequesting(true);
    targetWindow.postMessage(
      {
        type: "getShopCartSelectedIds",
      },
      window.location.origin,
    );

    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      setRequesting(false);
      toast.error(t.inputBox.shopCartNoResponse);
    }, REQUEST_TIMEOUT_MS);
  }, [t.inputBox.shopCartNoResponse]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[min(760px,calc(100vw-2rem))] gap-4 p-0">
        <DialogHeader className="px-5 pt-5">
          <DialogTitle>{t.inputBox.shopCartDialogTitle}</DialogTitle>
          <DialogDescription>
            {t.inputBox.shopCartDialogDescription}
          </DialogDescription>
        </DialogHeader>

        <div className="px-5">
          <iframe
            ref={iframeRef}
            className="h-[440px] w-full rounded-md border bg-white"
            src={SHOP_CART_IFRAME_SRC}
            title={t.inputBox.shopCartDialogTitle}
          />
        </div>

        <DialogFooter className="border-t px-5 py-4">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            {t.common.cancel}
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
