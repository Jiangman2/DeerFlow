import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { createElement, useState } from "react";
import { toast } from "sonner";

import { NewShopCartDialog } from "@/components/workspace/shop-cart-attachments/new-shop-cart-dialog";
import { ShopCartAttachmentButton } from "@/components/workspace/shop-cart-attachments/shop-cart-attachment-button";
import { ensureDataLakeAuthorization } from "@/core/data-lake/auth";
import { fetchNewShopCartFiles } from "@/core/shop-cart/new-api";

rs.mock("@/components/ai-elements/prompt-input", () => ({
  PromptInputButton: "button",
  usePromptInputAttachments: () => ({ add: () => undefined }),
}));

rs.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: "div",
  DropdownMenuContent: "div",
  DropdownMenuItem: ({
    children,
    onSelect,
    disabled,
  }: {
    children: React.ReactNode;
    onSelect?: () => void;
    disabled?: boolean;
  }) => createElement("button", { onClick: onSelect, disabled }, children),
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
        dataLakeAuthorizing: "Authorizing…",
        dataLakeAuthorizingDescription: "Verifying Data Lake access",
        dataLakeAuthorizationTimeout: "Authorization timed out",
        dataLakeLoginDescription: "Sign in and return to retry",
        dataLakeOpenLogin: "Open Data Lake",
        dataLakeLoginRetry: "I have signed in, retry",
        dataLakeLoginLater: "Later",
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

function Harness({ enabled = false }: { enabled?: boolean }) {
  const [context, setContext] = useState<
    React.ComponentProps<typeof ShopCartAttachmentButton>["context"]
  >({
    mode: undefined,
    datalake_enabled: enabled,
  });
  return createElement(ShopCartAttachmentButton, {
    context,
    onContextChange: setContext,
    onAddFiles: (files) => files.length,
  });
}

describe("Data Lake authorization feedback", () => {
  beforeEach(() => {
    rs.clearAllMocks();
  });
  afterEach(() => cleanup());

  it("keeps feedback visible while authorizing and prevents duplicate requests", async () => {
    let finish!: (value: { ok: true }) => void;
    rs.mocked(ensureDataLakeAuthorization).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(createElement(Harness));
    fireEvent.click(screen.getByText("Use Data Lake"));
    expect(screen.getByText("Authorizing…").closest("button")?.disabled).toBe(
      true,
    );
    expect(toast.loading).toHaveBeenCalledWith("Verifying Data Lake access");
    fireEvent.click(screen.getByText("Use Data Lake"));
    expect(ensureDataLakeAuthorization).toHaveBeenCalledTimes(1);
    await act(async () => finish({ ok: true }));
    expect(screen.getByText("Data Lake file list")).toBeDefined();
    expect(toast.dismiss).toHaveBeenCalledWith("toast-id");
  });

  it("provides a safe login link and resumes enabling only after successful retry", async () => {
    rs.mocked(ensureDataLakeAuthorization)
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        code: "data_lake_login_required",
      })
      .mockResolvedValueOnce({ ok: true });
    render(createElement(Harness));
    fireEvent.click(screen.getByText("Use Data Lake"));
    const link = await screen.findByRole("link", { name: "Open Data Lake" });
    expect(link.getAttribute("href")).toBe("https://datalake.cnooc/");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(fetchNewShopCartFiles).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("I have signed in, retry"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("Data Lake file list")).toBeDefined();
    expect(ensureDataLakeAuthorization).toHaveBeenCalledTimes(2);
  });

  it("preserves enabled state and reloads files after a list login failure", async () => {
    rs.mocked(ensureDataLakeAuthorization).mockResolvedValue({ ok: true });
    rs.mocked(fetchNewShopCartFiles)
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        code: "data_lake_login_required",
        message: "Sign in",
      })
      .mockResolvedValueOnce({ ok: true, files: [] });
    render(createElement(Harness, { enabled: true }));
    fireEvent.click(screen.getByText("Data Lake file list"));
    await screen.findByRole("dialog");
    expect(screen.getByText("Do not use Data Lake")).toBeDefined();
    fireEvent.click(screen.getByText("I have signed in, retry"));
    await waitFor(() => expect(fetchNewShopCartFiles).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(rs.mocked(NewShopCartDialog).mock.calls.at(-1)?.[0].open).toBe(true);
  });

  it("does not offer login for permission denial or timeout and restores the button", async () => {
    rs.mocked(ensureDataLakeAuthorization)
      .mockResolvedValueOnce({
        ok: false,
        status: 403,
        code: "data_lake_permission_denied",
      })
      .mockResolvedValueOnce({
        ok: false,
        code: "data_lake_authorization_timeout",
      });
    render(createElement(Harness));
    fireEvent.click(screen.getByText("Use Data Lake"));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("No Data Lake permission"),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByText("Use Data Lake"));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Authorization timed out"),
    );
    expect(screen.getByText("Data Lake").closest("button")?.disabled).toBe(
      false,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
