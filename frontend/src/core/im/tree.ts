import type { AgentThread } from "@/core/threads/types";
import { channelSourceOfThread } from "@/core/threads/utils";

import type { ImGroupNode, ImSessionTree, ImThreadItem } from "./types";

export const IM_QUERY_KEY = ["im"] as const;

export type ImGroupStatusNotice = "left" | "dissolved";

export function isImThread(thread: Pick<AgentThread, "metadata">): boolean {
  return channelSourceOfThread(thread) !== null;
}

export function treeIsEmpty(tree: ImSessionTree | null | undefined): boolean {
  if (!tree) {
    return true;
  }
  const privateCount = tree.private?.threads?.length ?? 0;
  return privateCount === 0 && (tree.groups?.length ?? 0) === 0;
}

export function countTreeThreads(tree: ImSessionTree | null | undefined): number {
  if (!tree) {
    return 0;
  }
  return (tree.private?.threads?.length ?? 0) + (tree.groups?.length ?? 0);
}

export function groupStatusNotice(
  chatStatus: string | null | undefined,
): ImGroupStatusNotice | null {
  const normalized = (chatStatus ?? "").trim().toUpperCase();
  if (normalized === "LEFT") {
    return "left";
  }
  if (normalized === "DISSOLVED") {
    return "dissolved";
  }
  return null;
}

export function threadDisplayTitle(item: ImThreadItem): string {
  const title = item.title?.trim();
  if (title) {
    return title;
  }
  const chatName = item.chat_name?.trim();
  if (chatName) {
    return chatName;
  }
  return item.thread_id;
}

export function groupDisplayName(node: Pick<ImGroupNode, "name" | "chat_id">) {
  const name = node.name?.trim();
  return name ?? node.chat_id;
}

export function mergeGroupThreadPage(
  node: ImGroupNode,
  threads: readonly ImThreadItem[],
): ImGroupNode {
  const seen = new Set(node.threads.map((thread) => thread.thread_id));
  const merged = [...node.threads];
  for (const thread of threads) {
    if (seen.has(thread.thread_id)) {
      continue;
    }
    seen.add(thread.thread_id);
    merged.push(thread);
  }
  return { ...node, threads: merged };
}