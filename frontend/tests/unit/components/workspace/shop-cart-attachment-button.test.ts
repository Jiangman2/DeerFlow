import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { toast } from "sonner";

import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { NewShopCartDialog } from "@/components/workspace/shop-cart-attachments/new-shop-cart-dialog";
import { ShopCartAttachmentButton } from "@/components/workspace/shop-cart-attachments/shop-cart-attachment-button";
import { ensureDataLakeAuthorization } from "@/core/data-lake/auth";
import type { DownloadableNewShopCartFile } from "@/core/shop-cart/api-files";
import {
  downloadNewShopCartFile,
  fetchNewShopCartFiles,
} from "@/core/shop-cart/new-api";
import { validateIncomingUploadFiles } from "@/core/uploads/file-validation";

rs.mock("@/components/ai-elements/prompt-input", () => ({
  PromptInputButton: "button",
  usePromptInputAttachments: () => ({ add: () => undefined }),
}));

rs.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: "div",
  DropdownMenuContent: "div",
  DropdownMenuItem: rs.fn(
    ({ children }: { children: React.ReactNode }) => children,
  ),
  DropdownMenuSeparator: "hr",
  DropdownMenuTrigger: "div",
}));

rs.mock("@/components/workspace/tooltip", () => ({
  Tooltip: "span",
}));

rs.mock(
  "@/components/workspace/shop-cart-attachments/new-shop-cart-dialog",
  () => ({ NewShopCartDialog: rs.fn(() => "new-shop-cart-dialog") }),
);

rs.mock("@/core/shop-cart/new-api", () => ({
  downloadNewShopCartFile: rs.fn(),
  fetchNewShopCartFiles: rs.fn(),
}));

rs.mock("@/core/data-lake/auth", () => ({
  ensureDataLakeAuthorization: rs.fn(),
}));

rs.mock("sonner", () => ({
  toast: {
    dismiss: rs.fn(),
    error: rs.fn(),
    loading: rs.fn(() => "toast-id"),
    success: rs.fn(),
    warning: rs.fn(),
  },
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      inputBox: {
        addShopCartAttachments: "Add from Data Lake",
        shopCart: "Data Lake",
        dataLakeFileList: "Data Lake file list",
        disableDataLake: "Do not use Data Lake",
        enableDataLake: "Use Data Lake",
        dataLakeAuthorizationFailed: "Authorization failed",
        dataLakePermissionDenied: "No Data Lake permission",
        dataLakeLoginRequired: "Please sign in to Data Lake first.",
        newShopCartLoadFailed: "Load failed",
        shopCartAddedFiles: (count: number) => `Added ${count}`,
        shopCartDownloadFailed: "Download failed",
        shopCartDownloading: "Downloading",
        shopCartEmptySelection: "Select files",
        shopCartPartialDownloadFailed: (count: number) => `Failed ${count}`,
      },
    },
  }),
}));

describe("ShopCartAttachmentButton", () => {
  beforeEach(() => {
    rs.mocked(NewShopCartDialog).mockClear();
    rs.mocked(DropdownMenuItem).mockClear();
    rs.mocked(ensureDataLakeAuthorization).mockReset();
    rs.mocked(downloadNewShopCartFile).mockReset();
    rs.mocked(fetchNewShopCartFiles).mockReset();
    rs.mocked(toast.dismiss).mockClear();
    rs.mocked(toast.error).mockClear();
    rs.mocked(toast.loading).mockClear();
    rs.mocked(toast.success).mockClear();
    rs.mocked(toast.warning).mockClear();
  });

  it("renders the file list and dialog when Data Lake is enabled", () => {
    const html = renderDataLakeControl(true);

    expect(html).toContain("Data Lake file list");
    expect(html).toContain("Do not use Data Lake");
    expect(html).toContain("new-shop-cart-dialog");
    expect(html).toContain("border-[#6d5dfc]/40 bg-[#f4f2ff]");
  });

  it("hides the file list and dialog when Data Lake is disabled", () => {
    const html = renderDataLakeControl(false);

    expect(html).toContain("Use Data Lake");
    expect(html).not.toContain("Data Lake file list");
    expect(html).not.toContain("new-shop-cart-dialog");
    expect(html).not.toContain("border-[#6d5dfc]/40 bg-[#f4f2ff]");
  });

  it("treats a missing Data Lake setting as disabled", () => {
    const html = renderDataLakeControl(undefined);

    expect(html).toContain("Use Data Lake");
    expect(html).not.toContain("Data Lake file list");
  });

  it("reauthorizes before opening a persisted enabled Data Lake", async () => {
    rs.mocked(ensureDataLakeAuthorization).mockResolvedValue({ ok: true });
    rs.mocked(fetchNewShopCartFiles).mockResolvedValue({ ok: true, files: [] });
    renderDataLakeControl(true);

    const fileListItem = rs
      .mocked(DropdownMenuItem)
      .mock.calls.find(([props]) =>
        hasChildText(
          (props as { children?: React.ReactNode }).children,
          "Data Lake file list",
        ),
      )?.[0] as { onSelect?: () => void | Promise<void> } | undefined;

    await fileListItem?.onSelect?.();

    expect(ensureDataLakeAuthorization).toHaveBeenCalledTimes(1);
    expect(fetchNewShopCartFiles).toHaveBeenCalledTimes(1);
  });

  it("does not load files when persisted Data Lake reauthorization fails", async () => {
    rs.mocked(ensureDataLakeAuthorization).mockResolvedValue({
      ok: false,
      message: "Denied",
    });
    renderDataLakeControl(true);

    const fileListItem = rs
      .mocked(DropdownMenuItem)
      .mock.calls.find(([props]) =>
        hasChildText(
          (props as { children?: React.ReactNode }).children,
          "Data Lake file list",
        ),
      )?.[0] as { onSelect?: () => void | Promise<void> } | undefined;

    await fileListItem?.onSelect?.();

    expect(fetchNewShopCartFiles).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("Denied");
  });

  it("shows a dedicated message when the account has no Data Lake permission", async () => {
    rs.mocked(ensureDataLakeAuthorization).mockResolvedValue({
      ok: false,
      status: 403,
      code: "data_lake_permission_denied",
      message: "The current account does not have Data Lake access.",
    });
    renderDataLakeControl(false);

    const enableItem = rs
      .mocked(DropdownMenuItem)
      .mock.calls.find(([props]) =>
        hasChildText(
          (props as { children?: React.ReactNode }).children,
          "Use Data Lake",
        ),
      )?.[0] as { onSelect?: () => void | Promise<void> } | undefined;
    await enableItem?.onSelect?.();

    expect(toast.error).toHaveBeenCalledWith("No Data Lake permission");
  });

  it("disables a persisted Data Lake after an identity mismatch", async () => {
    const onContextChange = rs.fn();
    rs.mocked(ensureDataLakeAuthorization).mockResolvedValue({
      ok: false,
      status: 403,
      message: "Identity mismatch",
    });
    renderToStaticMarkup(
      createElement(ShopCartAttachmentButton, {
        context: {
          model_name: "latest-model",
          mode: "thinking",
          datalake_enabled: true,
        },
        onAddFiles: (files) => files.length,
        onContextChange,
      }),
    );

    const fileListItem = rs
      .mocked(DropdownMenuItem)
      .mock.calls.find(([props]) =>
        hasChildText(
          (props as { children?: React.ReactNode }).children,
          "Data Lake file list",
        ),
      )?.[0] as { onSelect?: () => void | Promise<void> } | undefined;
    await fileListItem?.onSelect?.();

    expect(onContextChange).toHaveBeenCalledWith({
      model_name: "latest-model",
      mode: "thinking",
      datalake_enabled: false,
    });
    expect(fetchNewShopCartFiles).not.toHaveBeenCalled();
  });

  it("disables Data Lake when the file API rejects a stale user cookie", async () => {
    const onContextChange = rs.fn();
    rs.mocked(fetchNewShopCartFiles).mockResolvedValue({
      ok: false,
      status: 401,
      message: "Unauthorized",
    });
    renderToStaticMarkup(
      createElement(ShopCartAttachmentButton, {
        context: {
          model_name: "latest-model",
          mode: "thinking",
          datalake_enabled: true,
        },
        onAddFiles: (files) => files.length,
        onContextChange,
      }),
    );

    const dialogProps = rs.mocked(NewShopCartDialog).mock.calls.at(-1)?.[0];
    expect(dialogProps).toBeDefined();
    const refresh = dialogProps!.onRefresh as unknown as () => Promise<void>;
    await refresh();

    expect(onContextChange).toHaveBeenCalledWith({
      model_name: "latest-model",
      mode: "thinking",
      datalake_enabled: false,
    });
  });

  it("keeps Data Lake enabled when the upstream platform login is missing", async () => {
    const onContextChange = rs.fn();
    rs.mocked(fetchNewShopCartFiles).mockResolvedValue({
      ok: false,
      status: 401,
      code: "data_lake_login_required",
      message: "请先登录数据湖",
    });
    renderToStaticMarkup(
      createElement(ShopCartAttachmentButton, {
        context: {
          model_name: "latest-model",
          mode: "thinking",
          datalake_enabled: true,
        },
        onAddFiles: (files) => files.length,
        onContextChange,
      }),
    );

    const dialogProps = rs.mocked(NewShopCartDialog).mock.calls.at(-1)?.[0];
    expect(dialogProps).toBeDefined();
    const refresh = dialogProps!.onRefresh as unknown as () => Promise<void>;
    await refresh();

    expect(toast.error).toHaveBeenCalledWith(
      "Please sign in to Data Lake first.",
    );
    expect(onContextChange).not.toHaveBeenCalled();
  });

  it("does not report success when every downloaded file is rejected", async () => {
    const downloadedFile = new File(["zip"], "oversized.zip", {
      type: "application/zip",
    });
    Object.defineProperty(downloadedFile, "size", {
      value: 60 * 1024 * 1024,
    });
    rs.mocked(downloadNewShopCartFile).mockResolvedValue({
      ok: true,
      file: downloadedFile,
    });
    const onAddFiles = rs.fn(
      (files: File[]) =>
        validateIncomingUploadFiles([], files, {
          max_files: 10,
          max_file_size: 50 * 1024 * 1024,
          max_total_size: 100 * 1024 * 1024,
        }).accepted.length,
    );
    renderDataLakeControl(true, onAddFiles);

    const dialogProps = rs.mocked(NewShopCartDialog).mock.calls.at(-1)?.[0];
    expect(dialogProps).toBeDefined();

    const shouldClose = await dialogProps!.onSelectedFiles([downloadableFile]);

    expect(onAddFiles).toHaveBeenCalledWith([downloadedFile]);
    expect(toast.success).not.toHaveBeenCalled();
    expect(shouldClose).toBe(false);
  });

  it("reports the number of files actually accepted", async () => {
    const firstFile = new File(["first"], "first.zip", {
      type: "application/zip",
    });
    const secondFile = new File(["second"], "second.zip", {
      type: "application/zip",
    });
    rs.mocked(downloadNewShopCartFile)
      .mockResolvedValueOnce({ ok: true, file: firstFile })
      .mockResolvedValueOnce({ ok: true, file: secondFile });
    const onAddFiles = rs.fn(() => 1);
    renderDataLakeControl(true, onAddFiles);

    const dialogProps = rs.mocked(NewShopCartDialog).mock.calls.at(-1)?.[0];
    expect(dialogProps).toBeDefined();

    const shouldClose = await dialogProps!.onSelectedFiles([
      downloadableFile,
      { ...downloadableFile, id: "item-2", fileId: "file-2" },
    ]);

    expect(onAddFiles).toHaveBeenCalledWith([firstFile, secondFile]);
    expect(toast.success).toHaveBeenCalledWith("Added 1");
    expect(shouldClose).toBe(true);
  });
});

const downloadableFile: DownloadableNewShopCartFile = {
  id: "item-1",
  fileId: "file-1",
  userId: "user-1",
  isComplete: 1,
  createDate: "2026-08-11 10:00:00",
  fileName: "oversized.zip",
  type: 1,
  ptCode: "pt",
  isStore: 1,
  dacCode: null,
  num: 1,
  remark: null,
  tipsInfo: null,
  extra: null,
  errorInfo: null,
  shopType: 1,
};

function hasChildText(children: React.ReactNode, text: string): boolean {
  if (typeof children === "string") {
    return children.includes(text);
  }
  if (Array.isArray(children)) {
    return children.some(
      (child) => typeof child === "string" && child.includes(text),
    );
  }
  return false;
}

function renderDataLakeControl(
  datalakeEnabled: boolean | undefined,
  onAddFiles: (files: File[]) => number = (files) => files.length,
) {
  const props = {
    context: {
      model_name: undefined,
      mode: undefined,
      datalake_enabled: datalakeEnabled,
    },
    onAddFiles,
  };
  return renderToStaticMarkup(createElement(ShopCartAttachmentButton, props));
}
