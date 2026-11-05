import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ComposerAttachment } from "@/components/workspace/composer-attachment";

const { remove } = rs.hoisted(() => ({ remove: rs.fn() }));
rs.mock("@/components/ai-elements/prompt-input", () => ({
  usePromptInputAttachments: () => ({ remove }),
}));
rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { preview: "Preview" },
      uploads: {
        attachment: "Attachment",
        removeAttachment: "Remove attachment",
      },
    },
  }),
}));

const data = {
  id: "file-1",
  type: "file" as const,
  filename: "长名称附件.png",
  mediaType: "image/png",
  url: "blob:local-preview",
};
afterEach(() => {
  cleanup();
  remove.mockClear();
});

describe("Composer attachment interactions", () => {
  it("keeps the attachment immutable while submission is locked, then allows removal after failure", () => {
    const { rerender } = render(<ComposerAttachment data={data} disabled />);
    fireEvent.click(
      screen.getByRole("button", { name: "Remove attachment: 长名称附件.png" }),
    );
    expect(remove).not.toHaveBeenCalled();
    rerender(<ComposerAttachment data={data} disabled={false} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Remove attachment: 长名称附件.png" }),
    );
    expect(remove).toHaveBeenCalledWith("file-1");
  });

  it("shows the full file name and selected image in a dismissible preview without removing it", () => {
    render(<ComposerAttachment data={data} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Preview: 长名称附件.png" }),
    );
    expect(screen.getByRole("dialog", { name: data.filename })).toBeTruthy();
    expect(
      screen.getByRole("img", { name: data.filename }).getAttribute("src"),
    ).toBe(data.url);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(remove).not.toHaveBeenCalled();
  });
});
