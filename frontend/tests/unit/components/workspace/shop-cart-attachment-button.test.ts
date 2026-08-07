import { describe, expect, it, rs } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ShopCartAttachmentButton } from "@/components/workspace/shop-cart-attachments/shop-cart-attachment-button";

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
  () => ({ NewShopCartDialog: "section" }),
);

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      inputBox: {
        addShopCartAttachments: "Add from Data Lake",
        shopCart: "Data Lake",
        dataLakeFileList: "Data Lake file list",
        disableDataLake: "Do not use Data Lake",
        enableDataLake: "Use Data Lake",
      },
    },
  }),
}));

describe("ShopCartAttachmentButton", () => {
  it("renders the file list and dialog when Data Lake is enabled", () => {
    const html = renderDataLakeControl(true);

    expect(html).toContain("Data Lake file list");
    expect(html).toContain("Do not use Data Lake");
    expect(html).toContain("<section");
  });

  it("hides the file list and dialog when Data Lake is disabled", () => {
    const html = renderDataLakeControl(false);

    expect(html).toContain("Use Data Lake");
    expect(html).not.toContain("Data Lake file list");
    expect(html).not.toContain("<section");
  });
});

function renderDataLakeControl(datalakeEnabled: boolean) {
  return renderToStaticMarkup(
    createElement(ShopCartAttachmentButton, {
      context: {
        model_name: undefined,
        mode: undefined,
        datalake_enabled: datalakeEnabled,
      },
    }),
  );
}
