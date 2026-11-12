"use client";

import {
  CheckIcon,
  DatabaseIcon,
  FileIcon,
  RefreshCwIcon,
  SearchIcon,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
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
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleFiles = useMemo(
    () =>
      files.filter((file) =>
        file.fileName.toLocaleLowerCase().includes(normalizedQuery),
      ),
    [files, normalizedQuery],
  );

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
      <DialogContent className="cnooc-material-dialog flex flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="cnooc-material-heading">
          <div className="text-muted-foreground mb-1 flex items-center gap-2">
            <DatabaseIcon className="size-4" aria-hidden="true" />
            <span className="text-xs">{t.inputBox.shopCart}</span>
          </div>
          <DialogTitle>{t.inputBox.shopCartDialogTitle}</DialogTitle>
          <DialogDescription className="leading-relaxed">
            {t.inputBox.newShopCartDialogDescription}
          </DialogDescription>
        </DialogHeader>

        <div className="cnooc-material-search flex shrink-0 items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <SearchIcon
              className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
              aria-hidden="true"
            />
            <Input
              aria-label={t.inputBox.newShopCartSearch}
              placeholder={t.inputBox.newShopCartSearch}
              className="pl-9"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <Button
            disabled={loading || adding}
            size="sm"
            type="button"
            variant="outline"
            onClick={onRefresh}
          >
            <RefreshCwIcon
              data-icon="inline-start"
              className={cn(
                loading && "animate-spin motion-reduce:animate-none",
              )}
            />
            {t.inputBox.newShopCartRefresh}
          </Button>
        </div>

        <ScrollArea
          className="cnooc-material-results min-h-0 flex-1"
          aria-busy={loading}
        >
          <div className="flex flex-col gap-1 p-2">
            {visibleFiles.length === 0 ? (
              <div
                className="text-muted-foreground flex h-48 items-center justify-center px-5 text-sm"
                role="status"
              >
                {loading
                  ? t.inputBox.newShopCartLoading
                  : normalizedQuery
                    ? t.inputBox.newShopCartNoMatches
                    : t.inputBox.newShopCartEmpty}
              </div>
            ) : (
              visibleFiles.map((file) => {
                const selectable = isDownloadableNewShopCartFile(file);
                const selected = selectable && selectedIds.has(file.id);
                return (
                  <button
                    className="cnooc-material-row"
                    aria-pressed={selected}
                    disabled={!selectable || adding}
                    key={file.id}
                    type="button"
                    onClick={() => toggleFile(file)}
                  >
                    <span className="cnooc-material-check" aria-hidden="true">
                      {selected ? <CheckIcon className="size-3.5" /> : null}
                    </span>
                    <span className="min-w-0">
                      <span className="flex min-w-0 items-center gap-2">
                        <FileIcon className="text-muted-foreground size-4 shrink-0" />
                        <span
                          className="line-clamp-2 text-sm font-medium break-all"
                          title={file.fileName}
                        >
                          {file.fileName}
                        </span>
                      </span>
                      <span className="text-muted-foreground mt-1 block text-xs">
                        {file.createDate}
                      </span>
                    </span>
                    <Badge
                      variant="secondary"
                      className="cnooc-material-status"
                    >
                      {file.isComplete === 1
                        ? t.inputBox.newShopCartComplete
                        : t.inputBox.newShopCartProcessing}
                    </Badge>
                  </button>
                );
              })
            )}
          </div>
        </ScrollArea>

        <div className="cnooc-material-summary flex shrink-0 items-center justify-between gap-2 border-t">
          <span className="text-muted-foreground text-xs" role="status">
            {t.inputBox.newShopCartSelectionSummary(
              selectedFiles.length,
              files.length,
            )}
          </span>
          <Button
            variant="ghost"
            size="sm"
            type="button"
            disabled={adding || selectedFiles.length === 0}
            onClick={() => setSelectedIds(new Set())}
          >
            {t.inputBox.newShopCartClearSelection}
          </Button>
        </div>
        <DialogFooter className="cnooc-material-footer">
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
            {adding
              ? t.inputBox.shopCartAdding
              : t.inputBox.shopCartAddSelected}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
