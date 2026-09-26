"use client";

import { Alert, AlertDescription } from "@/components/ui/alert";
import type { EmployeeThreadPublication } from "@/core/agents/resources";
import { useI18n } from "@/core/i18n/hooks";

export function EmployeeVersionNotice({
  publication,
  error,
}: {
  publication?: EmployeeThreadPublication;
  error: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === "zh-CN";
  if (!error && !publication?.bound) return null;
  return (
    <Alert
      variant={error ? "destructive" : "default"}
      className="mb-2"
      data-testid="employee-version-notice"
    >
      <AlertDescription>
        {error ? (
          zh ? (
            "无法核验此对话的员工版本或授权，请刷新或联系发布者。不会改用其他版本。"
          ) : (
            "Cannot verify this conversation's employee version or authorization. Refresh or contact the publisher; no other version will be substituted."
          )
        ) : (
          <span title={publication?.revision}>
            {zh ? "当前对话固定版本：" : "Conversation pinned to version: "}
            {publication?.revision?.slice(0, 12)}
            {publication?.update_available
              ? zh
                ? "。有新发布版本，点击顶部“新对话”使用；此对话与历史记录保持不变。"
                : ". A new version is available. Use New chat above to start with it; this conversation and its history stay unchanged."
              : zh
                ? "。新发布不会自动改变此对话。"
                : ". Future publications do not change this conversation."}
          </span>
        )}
      </AlertDescription>
    </Alert>
  );
}
