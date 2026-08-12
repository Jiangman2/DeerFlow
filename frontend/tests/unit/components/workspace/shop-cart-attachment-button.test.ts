import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { toast } from "sonner";

import { NewShopCartDialog } from "@/components/workspace/shop-cart-attachments/new-shop-cart-dialog";
import { ShopCartAttachmentButton } from "@/components/workspace/shop-cart-attachments/shop-cart-attachment-button";
import type { DownloadableNewShopCartFile } from "@/core/shop-cart/api-files";
import { downloadNewShopCartFile } from "@/core/shop-cart/new-api";
import { validateIncomingUploadFiles } from "@/core/uploads/file-validation";

rs.mock("@/components/ai-elements/prompt-input", () => ({
  PromptInputButton: "button",
  usePromptInputAttachments: () => ({ add: () => undefined }),
}));

rs.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: "div",
  DropdownMenuContent: "div",
  DropdownMenuItem: "div",
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
    rs.mocked(downloadNewShopCartFile).mockReset();
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

function renderDataLakeControl(
  datalakeEnabled: boolean,
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
