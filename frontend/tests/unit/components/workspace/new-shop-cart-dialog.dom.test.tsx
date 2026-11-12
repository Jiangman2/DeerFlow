import { afterEach, expect, it, rs } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { NewShopCartDialog } from "@/components/workspace/shop-cart-attachments/new-shop-cart-dialog";
import { zhCN } from "@/core/i18n/locales/zh-CN";
import { parseNewShopCartListResponse } from "@/core/shop-cart/api-files";

rs.mock("@/core/i18n/hooks", () => ({ useI18n: () => ({ t: zhCN }) }));

afterEach(cleanup);

it("loads mixed records, shows failure reasons and adds only the selected ready file", async () => {
  const ready = {
    id: "ready",
    fileId: "download-id",
    userId: "test-user",
    isComplete: 1,
    createDate: "2026-11-11 10:00:00",
    fileName: "report.xlsx",
    type: 0,
    ptCode: "test",
    isStore: 0,
    dacCode: null,
    num: 1,
    remark: null,
    tipsInfo: null,
    extra: null,
    errorInfo: null,
    shopType: 0,
  };
  const failed = {
    ...ready,
    id: "failed",
    fileName: "files.zip",
    isComplete: -1,
    fileId: null,
    num: null,
    errorInfo: "文件下载异常",
  };
  const result = parseNewShopCartListResponse({
    code: 0,
    data: [
      failed,
      ready,
      {
        ...ready,
        id: "oversized",
        fileName: "oversized.zip",
        isComplete: 2,
        errorInfo: "上游大小限制",
      },
      { ...failed, id: "unknown", fileName: "unknown.zip", errorInfo: null },
      {
        ...failed,
        id: "pending",
        fileName: "pending.zip",
        isComplete: 0,
        errorInfo: null,
      },
    ],
  });
  expect(result.ok).toBe(true);
  if (!result.ok) throw new Error("Mixed list rejected");
  const onSelectedFiles = rs.fn();
  render(
    <NewShopCartDialog
      files={result.files}
      loading={false}
      open
      onOpenChange={rs.fn()}
      onRefresh={rs.fn()}
      onSelectedFiles={onSelectedFiles}
    />,
  );
  const failedRow = screen.getByRole("button", {
    name: /files.zip/,
  });
  expect(failedRow.hasAttribute("disabled")).toBe(true);
  expect(failedRow.textContent).toContain("文件下载异常");
  expect(failedRow.textContent).toContain("下载失败");
  const oversizedRow = screen.getByRole("button", { name: /oversized.zip/ });
  expect(oversizedRow.hasAttribute("disabled")).toBe(true);
  expect(oversizedRow.textContent).toContain("超大文件");
  expect(oversizedRow.textContent).toContain("上游大小限制");
  fireEvent.click(oversizedRow);
  expect(
    screen.getByRole("button", { name: /pending.zip/ }).textContent,
  ).toContain("下载中");
  expect(
    screen.getByRole("button", { name: /report.xlsx/ }).textContent,
  ).toContain("可下载");
  expect(screen.getByText("未提供失败原因")).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: /pending.zip/ })
      .hasAttribute("disabled"),
  ).toBe(true);
  fireEvent.click(failedRow);
  const addButton = screen.getByRole("button", {
    name: zhCN.inputBox.shopCartAddSelected,
  });
  expect(addButton.hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: /report.xlsx/ }));
  expect(addButton.hasAttribute("disabled")).toBe(false);
  fireEvent.click(addButton);
  await waitFor(() => expect(onSelectedFiles).toHaveBeenCalledWith([ready]));
});
