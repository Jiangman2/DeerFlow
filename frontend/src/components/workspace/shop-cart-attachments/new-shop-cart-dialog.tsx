"use client";

import { CheckIcon, FileIcon, RefreshCwIcon } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useI18n } from "@/core/i18n/hooks";
import {
  isDownloadableNewShopCartFile,
  type DownloadableNewShopCartFile,
  type NewShopCartFile,
} from "@/core/shop-cart/api-files";
import { cn } from "@/lib/utils";

type NewShopCartDialogProps = {
  files: NewShopCartFile[];
  loading: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRefresh: () => void;
  onSelectedFiles: (
    files: DownloadableNewShopCartFile[],
  ) => boolean | void | Promise<boolean | void>;
};

export function NewShopCartDialog({
  files,
  loading,
  open,
  onOpenChange,
  onRefresh,
  onSelectedFiles,
}: NewShopCartDialogProps) {
  const { t } = useI18n();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);

  const selectedFiles = useMemo(
    () =>
      files.filter(
        (file): file is DownloadableNewShopCartFile =>
          selectedIds.has(file.id) && isDownloadableNewShopCartFile(file),
      ),
    [files, selectedIds],
  );

  const toggleFile = useCallback((file: NewShopCartFile) => {
    if (!isDownloadableNewShopCartFile(file)) {
      return;
    }

    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(file.id)) {
        next.delete(file.id);
      } else {
        next.add(file.id);
      }
      return next;
    });
  }, []);

  const addSelectedFiles = useCallback(async () => {
    setAdding(true);
    try {
      const shouldClose = await onSelectedFiles(selectedFiles);
      if (shouldClose !== false) {
        onOpenChange(false);
        setSelectedIds(new Set());
      }
    } finally {
      setAdding(false);
    }
  }, [onOpenChange, onSelectedFiles, selectedFiles]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(760px,calc(100vh-2rem))] max-w-[min(920px,calc(100vw-2rem))] flex-col gap-0 p-0">
        <DialogHeader className="border-b px-5 py-4">
          <DialogTitle>{t.inputBox.shopCartDialogTitle}</DialogTitle>
          <DialogDescription>
            {t.inputBox.newShopCartDialogDescription}
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center justify-between gap-3 border-b px-5 py-3 text-sm">
          <span className="text-muted-foreground">
            {t.inputBox.newShopCartSelectionSummary(
              selectedFiles.length,
              files.length,
            )}
          </span>
          <Button
            disabled={loading || adding}
            size="sm"
            type="button"
            variant="outline"
            onClick={onRefresh}
          >
            <RefreshCwIcon className={cn("size-4", loading && "animate-spin")} />
            {t.inputBox.newShopCartRefresh}
          </Button>
        </div>

        <ScrollArea className="min-h-0 flex-1">
          <div className="divide-y">
            {files.length === 0 ? (
              <div className="flex h-48 items-center justify-center px-5 text-sm text-muted-foreground">
                {loading
                  ? t.inputBox.newShopCartLoading
                  : t.inputBox.newShopCartEmpty}
              </div>
            ) : (
              files.map((file) => {
                const selectable = isDownloadableNewShopCartFile(file);
                const selected = selectable && selectedIds.has(file.id);
                return (
                  <button
                    className={cn(
                      "grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 px-5 py-3 text-left transition-colors",
                      selectable
                        ? "hover:bg-accent/60"
                        : "cursor-not-allowed opacity-60",
                      selected && "bg-accent",
                    )}
                    disabled={!selectable}
                    key={file.id}
                    type="button"
                    onClick={() => toggleFile(file)}
                  >
                    <span
                      className={cn(
                        "flex size-5 items-center justify-center rounded border",
                        selected
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-border bg-background",
                      )}
                    >
                      {selected ? <CheckIcon className="size-3.5" /> : null}
                    </span>
                    <span className="min-w-0">
                      <span className="flex min-w-0 items-center gap-2">
                        <FileIcon className="size-4 shrink-0 text-muted-foreground" />
                        <span className="truncate text-sm font-medium">
                          {file.fileName}
                        </span>
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {file.createDate}
                      </span>
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {file.isComplete === 1
                        ? t.inputBox.newShopCartComplete
                        : t.inputBox.newShopCartProcessing}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </ScrollArea>

        <DialogFooter className="border-t px-5 py-4">
          <Button
            disabled={adding}
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            {t.common.cancel}
          </Button>
          <Button
            disabled={adding || loading || selectedFiles.length === 0}
            type="button"
            onClick={addSelectedFiles}
          >
            {adding ? t.inputBox.shopCartAdding : t.inputBox.shopCartAddSelected}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
