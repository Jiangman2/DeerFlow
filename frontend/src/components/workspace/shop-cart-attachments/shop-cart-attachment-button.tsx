"use client";

import {
  DatabaseIcon,
  DatabaseZapIcon,
  ListIcon,
  Loader2Icon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { PromptInputButton } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
  const [loginAction, setLoginAction] = useState<"enable" | "files" | null>(
    null,
  );
  const authorizationPending = useRef(false);
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
    if (!contextRef.current.datalake_enabled) {
      return;
    }
    const sequence = ++requestSequence.current;
    setNewShopCartLoading(true);
    const result = await fetchNewShopCartFiles();
    if (
      sequence !== requestSequence.current ||
      !contextRef.current.datalake_enabled
    ) {
      return;
    }
    setNewShopCartLoading(false);

    if (!result.ok) {
      const loginRequired = result.code === "data_lake_login_required";
      if (result.status === 401 && !loginRequired) {
        resetDataLakeUi();
        commitDataLakeEnabled(false);
      }
      if (loginRequired) {
        setNewShopCartOpen(false);
        setLoginAction("files");
      }
      toast.error(
        loginRequired
          ? t.inputBox.dataLakeLoginRequired
          : t.inputBox.newShopCartLoadFailed,
      );
      return;
    }

    setNewShopCartFiles(result.files);
  }, [
    commitDataLakeEnabled,
    resetDataLakeUi,
    t.inputBox.dataLakeLoginRequired,
    t.inputBox.newShopCartLoadFailed,
  ]);

  const authorizeDataLake = useCallback(
    async (action: "enable" | "files") => {
      if (authorizationPending.current || disabled) return;
      authorizationPending.current = true;
      setAuthorizing(true);
      const toastId = toast.loading(t.inputBox.dataLakeAuthorizingDescription);
      let authorized;
      try {
        authorized = await ensureDataLakeAuthorization();
      } finally {
        toast.dismiss(toastId);
        authorizationPending.current = false;
        setAuthorizing(false);
      }
      if (!authorized.ok) {
        if (authorized.code === "data_lake_login_required") {
          setNewShopCartOpen(false);
          setLoginAction(action);
          toast.error(t.inputBox.dataLakeLoginRequired);
          return;
        }
        setLoginAction(null);
        if (authorized.status === 401 || authorized.status === 403) {
          resetDataLakeUi();
          commitDataLakeEnabled(false);
        }
        toast.error(
          authorized.code === "data_lake_permission_denied"
            ? t.inputBox.dataLakePermissionDenied
            : authorized.code === "data_lake_authorization_timeout"
              ? t.inputBox.dataLakeAuthorizationTimeout
              : (authorized.message ?? t.inputBox.dataLakeAuthorizationFailed),
        );
        return;
      }
      setLoginAction(null);
      commitDataLakeEnabled(true);
      if (action === "files") {
        setNewShopCartOpen(true);
        await loadNewShopCartFiles();
      }
    },
    [
      commitDataLakeEnabled,
      disabled,
      loadNewShopCartFiles,
      resetDataLakeUi,
      t.inputBox,
    ],
  );

  const updateDataLakeEnabled = useCallback(
    async (enabled: boolean) => {
      if (enabled) {
        await authorizeDataLake("enable");
      } else {
        setLoginAction(null);
        resetDataLakeUi();
        commitDataLakeEnabled(false);
      }
    },
    [authorizeDataLake, commitDataLakeEnabled, resetDataLakeUi],
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
              aria-busy={authorizing}
              disabled={disabled || authorizing}
            >
              {authorizing ? (
                <Loader2Icon className="size-3 animate-spin motion-reduce:animate-none" />
              ) : (
                <DatabaseIcon className="size-3" />
              )}
              <span className="text-xs font-normal" aria-live="polite">
                {authorizing
                  ? t.inputBox.dataLakeAuthorizing
                  : t.inputBox.shopCart}
              </span>
            </PromptInputButton>
          </DropdownMenuTrigger>
        </Tooltip>
        <DropdownMenuContent align="start">
          {dataLakeEnabled ? (
            <>
              <DropdownMenuItem
                disabled={authorizing}
                onSelect={() => authorizeDataLake("files")}
              >
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
            <DropdownMenuItem
              disabled={authorizing}
              onSelect={() => updateDataLakeEnabled(true)}
            >
              <DatabaseIcon />
              {t.inputBox.enableDataLake}
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog
        open={loginAction !== null}
        onOpenChange={(open) => {
          if (!open && !authorizing) setLoginAction(null);
        }}
      >
        <DialogContent showCloseButton={!authorizing}>
          <DialogHeader>
            <DialogTitle>{t.inputBox.dataLakeLoginRequired}</DialogTitle>
            <DialogDescription>
              {t.inputBox.dataLakeLoginDescription}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="sm:flex-wrap">
            <Button
              type="button"
              variant="ghost"
              disabled={authorizing}
              onClick={() => setLoginAction(null)}
            >
              {t.inputBox.dataLakeLoginLater}
            </Button>
            <Button variant="outline" asChild>
              <a
                href="https://datalake.cnooc/"
                target="_blank"
                rel="noopener noreferrer"
              >
                {t.inputBox.dataLakeOpenLogin}
              </a>
            </Button>
            <Button
              type="button"
              disabled={authorizing || disabled}
              onClick={() => {
                if (loginAction) void authorizeDataLake(loginAction);
              }}
            >
              {authorizing
                ? t.inputBox.dataLakeAuthorizing
                : t.inputBox.dataLakeLoginRetry}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
