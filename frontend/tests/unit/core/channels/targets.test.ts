import { describe, expect, test } from "@rstest/core";

import {
  chatIdOfFeishuConnection,
  feishuSendTargets,
  personalFeishuTarget,
} from "@/core/channels/targets";
import type { ChannelConnection } from "@/core/channels/types";

function connection(
  overrides: Partial<ChannelConnection> = {},
): ChannelConnection {
  return {
    id: "connection-1",
    provider: "feishu",
    status: "connected",
    scopes: [],
    metadata: {},
    ...overrides,
  } as ChannelConnection;
}

describe("feishuSendTargets", () => {
  test("keeps only live 海能 Work bindings", () => {
    const targets = feishuSendTargets([
      connection({
        id: "p2p",
        workspace_id: "oc-p2p",
        metadata: { chat_id: "oc-p2p", chat_type: "p2p" },
      }),
      connection({ id: "telegram", provider: "telegram" }),
      connection({
        id: "revoked",
        status: "revoked",
        metadata: { chat_id: "oc-revoked", chat_type: "p2p" },
      }),
    ]);

    expect(targets).toHaveLength(1);
    expect(targets[0]?.chatId).toBe("oc-p2p");
  });

  test("offers the personal chat first even when a group binding is newer", () => {
    // The export endpoint used to pick the newest binding by recency, which
    // silently sent a private conversation into a group chat. The menu must
    // therefore lead with the personal chat regardless of input order.
    const targets = feishuSendTargets([
      connection({
        id: "newer-group",
        workspace_id: "oc-group",
        workspace_name: "项目群",
        metadata: { chat_id: "oc-group", chat_type: "group" },
      }),
      connection({
        id: "older-p2p",
        workspace_id: "oc-p2p",
        workspace_name: "本人私聊",
        metadata: { chat_id: "oc-p2p", chat_type: "p2p" },
      }),
    ]);

    expect(targets.map((target) => target.chatId)).toEqual([
      "oc-p2p",
      "oc-group",
    ]);
    expect(targets[0]?.isPersonal).toBe(true);
    expect(targets[1]?.isPersonal).toBe(false);
    expect(targets[0]?.label).toBe("本人私聊");
    expect(targets[1]?.label).toBe("项目群");
  });

  test("treats a binding without a chat type as not personal", () => {
    // Older bindings predate the stored chat type. Assuming p2p would recreate
    // the guessing bug, so they are shown as explicit targets instead.
    const targets = feishuSendTargets([
      connection({
        id: "legacy",
        workspace_id: "oc-legacy",
        metadata: { chat_id: "oc-legacy" },
      }),
    ]);

    expect(targets).toHaveLength(1);
    expect(targets[0]?.isPersonal).toBe(false);
  });

  test("labels a target by workspace name, chat name, then chat id", () => {
    const targets = feishuSendTargets([
      connection({
        id: "named-workspace",
        workspace_name: "工作区名称",
        metadata: {
          chat_id: "oc-1",
          chat_type: "p2p",
          chat_name: "元数据名称",
        },
      }),
      connection({
        id: "named-metadata",
        workspace_name: "   ",
        metadata: {
          chat_id: "oc-2",
          chat_type: "p2p",
          chat_name: "元数据名称",
        },
      }),
      connection({
        id: "unnamed",
        metadata: { chat_id: "oc-3", chat_type: "p2p" },
      }),
    ]);

    expect(targets.map((target) => target.label)).toEqual([
      "工作区名称",
      "元数据名称",
      "oc-3",
    ]);
  });

  test("dedupes chats bound more than once", () => {
    const targets = feishuSendTargets([
      connection({
        id: "first",
        metadata: { chat_id: "oc-p2p", chat_type: "p2p" },
      }),
      connection({
        id: "second",
        metadata: { chat_id: "oc-p2p", chat_type: "p2p" },
      }),
    ]);

    expect(targets).toHaveLength(1);
  });

  test("drops bindings without any chat id", () => {
    const targets = feishuSendTargets([
      connection({ id: "no-chat", metadata: {} }),
    ]);

    expect(targets).toEqual([]);
  });
});

describe("chatIdOfFeishuConnection", () => {
  test("prefers metadata chat id and falls back to the workspace id", () => {
    expect(
      chatIdOfFeishuConnection(
        connection({
          workspace_id: "oc-workspace",
          metadata: { chat_id: "oc-metadata" },
        }),
      ),
    ).toBe("oc-metadata");

    expect(
      chatIdOfFeishuConnection(
        connection({ workspace_id: "oc-workspace", metadata: {} }),
      ),
    ).toBe("oc-workspace");

    expect(
      chatIdOfFeishuConnection(connection({ metadata: { chat_id: "  " } })),
    ).toBeNull();
  });
});

describe("personalFeishuTarget", () => {
  test("returns the personal chat only when one is bound", () => {
    const groupOnly = feishuSendTargets([
      connection({
        workspace_id: "oc-group",
        metadata: { chat_id: "oc-group", chat_type: "group" },
      }),
    ]);
    expect(personalFeishuTarget(groupOnly)).toBeNull();

    const withPersonal = feishuSendTargets([
      connection({
        workspace_id: "oc-group",
        metadata: { chat_id: "oc-group", chat_type: "group" },
      }),
      connection({
        workspace_id: "oc-p2p",
        metadata: { chat_id: "oc-p2p", chat_type: "p2p" },
      }),
    ]);
    expect(personalFeishuTarget(withPersonal)?.chatId).toBe("oc-p2p");
  });
});
