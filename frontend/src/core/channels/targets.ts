import type { ChannelConnection } from "./types";

export const FEISHU_PROVIDER = "feishu";

/** A chat the user can explicitly choose to receive an export. */
export interface FeishuSendTarget {
  chatId: string;
  label: string;
  isPersonal: boolean;
}

function metadataString(
  metadata: Record<string, unknown>,
  key: string,
): string {
  const value = metadata[key];
  return typeof value === "string" ? value.trim() : "";
}

/** Chat id recorded on a binding (`metadata.chat_id` first, then workspace id). */
export function chatIdOfFeishuConnection(
  connection: ChannelConnection,
): string | null {
  const metadata = connection.metadata ?? {};
  const chatId = metadataString(metadata, "chat_id");
  if (chatId) {
    return chatId;
  }
  const workspaceId = (connection.workspace_id ?? "").trim();
  return workspaceId || null;
}

/**
 * Resolve the caller's live 海能 Work chats into explicit send targets.
 *
 * Export must never *guess* a destination, so the UI lists the chats the user
 * is actually bound to and the personal chat comes first: it is both the safe
 * default and the first choice. Bindings without a chat id, revoked bindings,
 * and unknown chat types are dropped rather than shown as a silent fallback.
 */
export function feishuSendTargets(
  connections: readonly ChannelConnection[],
): FeishuSendTarget[] {
  const targets: FeishuSendTarget[] = [];
  const seen = new Set<string>();

  for (const connection of connections) {
    if (connection.provider !== FEISHU_PROVIDER) continue;
    if (connection.status !== "connected") continue;

    const chatId = chatIdOfFeishuConnection(connection);
    if (!chatId || seen.has(chatId)) continue;

    const metadata = connection.metadata ?? {};
    const chatType = metadataString(metadata, "chat_type").toLowerCase();
    const name =
      (connection.workspace_name ?? "").trim() ||
      metadataString(metadata, "chat_name") ||
      chatId;

    seen.add(chatId);
    targets.push({
      chatId,
      label: name,
      isPersonal: chatType === "p2p",
    });
  }

  return targets.sort(
    (left, right) => Number(right.isPersonal) - Number(left.isPersonal),
  );
}

/** The verified personal chat, if the caller has one bound. */
export function personalFeishuTarget(
  targets: readonly FeishuSendTarget[],
): FeishuSendTarget | null {
  return targets.find((target) => target.isPersonal) ?? null;
}
