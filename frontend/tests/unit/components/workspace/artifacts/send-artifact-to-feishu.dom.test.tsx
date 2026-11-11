import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { SendArtifactToFeishuButton } from "@/components/workspace/artifacts/send-artifact-to-feishu";
import { useChannelConnections } from "@/core/channels/hooks";
import type { ChannelConnection } from "@/core/channels/types";
import { sendThreadArtifactsToFeishu } from "@/core/threads/export";

const { toastSuccess, toastWarning, toastError } = rs.hoisted(() => ({
  toastSuccess: rs.fn(),
  toastWarning: rs.fn(),
  toastError: rs.fn(),
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ locale: "zh-CN" }),
}));
rs.mock("@/core/channels/hooks", () => ({ useChannelConnections: rs.fn() }));
rs.mock("@/core/threads/export", () => ({
  sendThreadArtifactsToFeishu: rs.fn(),
}));
rs.mock("sonner", () => ({
  toast: { success: toastSuccess, warning: toastWarning, error: toastError },
}));

afterEach(() => {
  cleanup();
  rs.clearAllMocks();
});

const boundConnection = {
  id: "conn-1",
  provider: "feishu",
  status: "connected",
  workspace_id: "oc_private_chat",
  workspace_name: "张三",
  metadata: { chat_id: "oc_private_chat", chat_type: "p2p" },
} as unknown as ChannelConnection;

function mockConnections(connections: ChannelConnection[]) {
  rs.mocked(useChannelConnections).mockReturnValue({
    connections,
    isLoading: false,
    error: null,
  } as never);
}

const OUTPUTS_FILE = "/mnt/user-data/outputs/report.xlsx";
const SKILL_FILE = "/mnt/user-data/outputs/bundle.skill";
const SCRATCH_FILE = "/mnt/user-data/workspace/scratch.xlsx";

describe("SendArtifactToFeishuButton", () => {
  test("stays disabled until a personal 海能 Work chat is bound", () => {
    mockConnections([]);

    render(
      <SendArtifactToFeishuButton
        threadId="thread-1"
        filepaths={[OUTPUTS_FILE]}
      />,
    );

    const button = screen.getByTestId("send-artifact-to-feishu");
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(sendThreadArtifactsToFeishu).not.toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  test("sends only outputs files and names the destination on success", async () => {
    mockConnections([boundConnection]);
    rs.mocked(sendThreadArtifactsToFeishu).mockResolvedValue({
      sent: true,
      filenames: ["report.xlsx"],
      skipped: [],
      target_chat_id: "oc_private_chat",
      target_chat_type: "p2p",
      target_name: "张三",
    });

    render(
      <SendArtifactToFeishuButton
        threadId="thread-1"
        filepaths={[OUTPUTS_FILE, SKILL_FILE, SCRATCH_FILE]}
      />,
    );

    const button = screen.getByTestId("send-artifact-to-feishu");
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);

    await screen.findByRole("button", { name: "发送海能 Work" });
    expect(sendThreadArtifactsToFeishu).toHaveBeenCalledWith("thread-1", [
      OUTPUTS_FILE,
    ]);
    expect(toastSuccess).toHaveBeenCalledWith("已发送到海能 Work · 张三");
  });

  test("reports files the gateway skipped", async () => {
    mockConnections([boundConnection]);
    rs.mocked(sendThreadArtifactsToFeishu).mockResolvedValue({
      sent: true,
      filenames: [],
      skipped: ["huge.zip"],
      target_chat_id: "oc_private_chat",
      target_chat_type: "p2p",
      target_name: null,
    });

    render(
      <SendArtifactToFeishuButton
        threadId="thread-1"
        filepaths={[OUTPUTS_FILE]}
      />,
    );
    fireEvent.click(screen.getByTestId("send-artifact-to-feishu"));

    await screen.findByRole("button", { name: "发送海能 Work" });
    expect(toastSuccess).toHaveBeenCalledWith("已发送到海能 Work · 本人私聊");
    expect(toastWarning).toHaveBeenCalledWith("以下文件未发送: huge.zip");
  });
});
