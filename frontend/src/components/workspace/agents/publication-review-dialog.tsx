"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { getAgent } from "@/core/agents/api";
import { usePublishAgent } from "@/core/agents/hooks";
import {
  activeGrant,
  checkPublication,
  requestEmployeeGrant,
  resourceLabel,
  type EmployeeResource,
} from "@/core/agents/resources";
import { useI18n } from "@/core/i18n/hooks";

export function PublicationReviewDialog({
  name,
  onClose,
}: {
  name: string;
  onClose: () => void;
}) {
  const { locale } = useI18n();
  const zh = locale === "zh-CN";
  const [confirmed, setConfirmed] = useState(false);
  const publish = usePublishAgent();
  const review = useQuery({
    queryKey: ["employee-publication-review", name],
    queryFn: async () => {
      const draft = await getAgent(name, "personal");
      if (!draft.draft_revision)
        throw new Error(
          zh
            ? "未取得草稿版本，请刷新。"
            : "Draft revision unavailable. Refresh before publishing.",
        );
      return {
        draft,
        check: await checkPublication(name, draft.draft_revision),
      };
    },
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 0,
    gcTime: 0,
  });
  const request = useMutation({
    mutationFn: (resource: EmployeeResource) =>
      requestEmployeeGrant(name, review.data!.check.draft_revision, resource),
    onSuccess: () => {
      setConfirmed(false);
      void review.refetch();
    },
    retry: false,
  });
  const rows = review.data?.check.resources ?? [];
  const grants = rows.map((row) => activeGrant(row.grants));
  const ready = !!review.data && grants.every(Boolean);
  const busy = review.isFetching || request.isPending || publish.isPending;
  const error = publish.error ?? request.error ?? review.error;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>
            {zh ? "发布到数字员工广场" : "Publish to Digital Employee Square"}
          </DialogTitle>
          <DialogDescription>
            {zh
              ? "发布的是固定版本，修改草稿不会改变已发布版本。Skill 仅供员工调用；知识仅供问答，不开放原文浏览或下载。"
              : "Publish a fixed revision. Draft edits do not change an existing release. Skills are invocation-only; knowledge is Q&A-only, with no source browsing or download."}
          </DialogDescription>
        </DialogHeader>
        {review.isFetching && (
          <p role="status">
            {zh
              ? "正在核验当前草稿和资源授权…"
              : "Checking current draft and resource permissions…"}
          </p>
        )}
        {error && (
          <p role="alert" className="text-destructive text-sm">
            {error.message}
          </p>
        )}
        {review.data && (
          <>
            <p className="font-medium">
              {review.data.draft.display_name ?? name}
            </p>
            <p className="text-muted-foreground text-sm">
              {zh ? "草稿版本：" : "Draft revision: "}
              {review.data.check.draft_revision.slice(0, 12)}
            </p>
            {!rows.length && (
              <p>
                {zh
                  ? "该版本没有绑定 Skill 或知识资源。"
                  : "This revision has no bound Skills or knowledge resources."}
              </p>
            )}
            <ul className="space-y-3">
              {rows.map((row, index) => {
                const approved = grants[index];
                const pending = row.grants.find(
                  (grant) => grant.state === "pending",
                );
                const grant = approved ?? pending;
                return (
                  <li
                    key={JSON.stringify(row.resource)}
                    className="rounded-lg border p-3"
                  >
                    <p className="text-sm font-medium break-all">
                      {resourceLabel(row.resource)}
                    </p>
                    <p className="my-2 text-sm">
                      {approved
                        ? zh
                          ? "已授权"
                          : "Approved"
                        : pending
                          ? zh
                            ? "等待审批"
                            : "Awaiting approval"
                          : zh
                            ? "需要共享调用授权"
                            : "Invocation approval required"}
                    </p>
                    {grant ? (
                      <Link
                        className="text-primary text-sm underline"
                        href={`/workspace/agents/grants/${grant.id}`}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {zh
                          ? "打开审批详情（可复制地址交给所有者）"
                          : "Open approval details (share this address with the owner)"}
                      </Link>
                    ) : (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => request.mutate(row.resource)}
                      >
                        {zh ? "申请授权" : "Request approval"}
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1 size-4 shrink-0 accent-blue-600"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
                disabled={!ready || busy}
              />
              <span>
                {zh
                  ? "我确认将上述资源用于全体广场用户的员工调用；不共享个人 API 密钥。"
                  : "I confirm this resource scope for all Square users invoking the employee. Personal API keys are not shared."}
              </span>
            </label>
          </>
        )}
        <DialogFooter>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => {
              setConfirmed(false);
              publish.reset();
              request.reset();
              void review.refetch();
            }}
          >
            {zh ? "刷新核验" : "Refresh checks"}
          </Button>
          <Button
            disabled={!ready || !confirmed || busy || !!publish.error}
            onClick={() => {
              if (!review.data) return;
              publish.mutate(
                {
                  name,
                  expectedRevision: review.data.draft.publication_revision,
                  expectedDraftRevision: review.data.check.draft_revision,
                  grantIds: grants.flatMap((grant) =>
                    grant ? [grant.id] : [],
                  ),
                },
                { onSuccess: onClose },
              );
            }}
          >
            {publish.isPending
              ? zh
                ? "发布中…"
                : "Publishing…"
              : zh
                ? "确认发布"
                : "Confirm publication"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
