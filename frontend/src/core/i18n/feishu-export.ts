/**
 * Copy for the Feishu / Haineng Work send menu.
 *
 * Kept in a feature module rather than the shared locale files: the release
 * provenance snapshots pin `locales/{en-US,zh-CN}.ts` byte-for-byte, so a new
 * menu label belongs beside its feature like `dashboard-navigation.ts` and
 * `data-qa-progress.ts` do.
 */
export function feishuExportCopy(locale: string) {
  return locale.startsWith("zh")
    ? {
        toFeishu: "发送到海能 Work",
        toFeishuSuccess: "已发送到海能 Work，请查收文件",
        files: "发送生成文件",
        filesSuccess: "生成文件已发送到海能 Work",
        noArtifacts: "当前对话没有可发送的生成文件",
        skipped: "以下文件未发送",
        toGroup: "发送到群聊",
        privateChat: "本人私聊",
        groupChat: "群聊",
        sendFile: "发送海能 Work",
        sendFileTooltip: "把这个文件发送到你的海能 Work 私聊",
        sendFileSuccess: "已发送到海能 Work",
        sending: "发送中…",
        bindFirst: "请先在「设置 → 渠道」绑定海能 Work",
      }
    : {
        toFeishu: "Send to Haineng Work",
        toFeishuSuccess: "Sent to Haineng Work",
        files: "Send generated files",
        filesSuccess: "Generated files sent to Haineng Work",
        noArtifacts: "This conversation has no generated files to send",
        skipped: "Not sent",
        toGroup: "Send to a group chat",
        privateChat: "your private chat",
        groupChat: "group chat",
        sendFile: "Send to Haineng Work",
        sendFileTooltip: "Send this file to your Haineng Work private chat",
        sendFileSuccess: "Sent to Haineng Work",
        sending: "Sending…",
        bindFirst: "Connect Haineng Work in Settings → Channels first",
      };
}
