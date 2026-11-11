/**
 * Copy for the Feishu IM session tree in the workspace sidebar.
 *
 * Kept in a feature module rather than the shared locale files: the release
 * provenance snapshots pin `locales/{en-US,zh-CN}.ts` byte-for-byte, so a new
 * menu label belongs beside its feature like `dashboard-navigation.ts`,
 * `data-qa-progress.ts` and `feishu-export.ts` do.
 */
export function imSessionCopy(locale: string) {
  return locale.startsWith("zh")
    ? {
        privateSection: "私聊",
        groupSection: "群聊",
        webChats: "Web 会话",
        imSearchPlaceholder: "搜索飞书会话",
        imEmptyPrivate: "暂无飞书私聊会话",
        imEmptyGroup: "暂无飞书群聊会话",
        imCurrentThread: "飞书当前会话",
        imGroupLeft: "已退出",
        imGroupDissolved: "已解散",
        imLoadMore: (count: number) => `查看全部 ${count} 条`,
        imShowAllGroups: (count: number) => `显示全部群聊（${count}）`,
        imSearchNoResult: "没有匹配的飞书会话",
        imMatchGroup: "群聊",
        imMatchThread: "会话",
        imSearchTruncated: "仅显示部分匹配结果，请缩小搜索范围。",
        imLoadFailed: "飞书会话加载失败",
        imRetry: "重试",
      }
    : {
        privateSection: "Private",
        groupSection: "Group",
        webChats: "Web chats",
        imSearchPlaceholder: "Search Feishu chats",
        imEmptyPrivate: "No Feishu private chats yet",
        imEmptyGroup: "No Feishu group chats yet",
        imCurrentThread: "Current Feishu conversation",
        imGroupLeft: "Left",
        imGroupDissolved: "Dissolved",
        imLoadMore: (count: number) => `Show all ${count}`,
        imShowAllGroups: (count: number) => `Show all groups (${count})`,
        imSearchNoResult: "No matching Feishu chats",
        imMatchGroup: "Group",
        imMatchThread: "Chat",
        imSearchTruncated: "Showing partial matches. Narrow the search.",
        imLoadFailed: "Failed to load Feishu chats",
        imRetry: "Retry",
      };
}
