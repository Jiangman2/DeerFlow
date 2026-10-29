import { afterEach, describe, expect, it, rs } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";

import { CopyButton } from "@/components/workspace/copy-button";

const { copy, error } = rs.hoisted(() => ({ copy: rs.fn(), error: rs.fn() }));
rs.mock("@/core/clipboard", () => ({ writeTextToClipboard: copy }));
rs.mock("sonner", () => ({ toast: { error } }));
rs.mock("@/components/workspace/tooltip", () => ({
  Tooltip: ({ children }: { children: ReactNode }) => children,
}));
rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      clipboard: {
        copyToClipboard: "Copy answer",
        copiedToClipboard: "Copied",
        failedToCopyToClipboard: "Copy failed",
      },
    },
  }),
}));
afterEach(() => {
  cleanup();
  rs.clearAllMocks();
});

describe("Answer copy button", () => {
  it("has a discoverable action name and reports success only after the exact answer is copied", async () => {
    copy.mockResolvedValue(true);
    render(<CopyButton clipboardData={"完整回答\n第二行"} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy answer" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy(),
    );
    expect(copy).toHaveBeenCalledWith("完整回答\n第二行");
  });
  it("keeps the copy action available and surfaces failure when clipboard access fails", async () => {
    copy.mockResolvedValue(false);
    render(<CopyButton clipboardData="完整回答" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy answer" }));
    await waitFor(() => expect(error).toHaveBeenCalledWith("Copy failed"));
    expect(screen.queryByRole("button", { name: "Copied" })).toBeNull();
    expect(screen.getByRole("button", { name: "Copy answer" })).toBeTruthy();
  });
});
