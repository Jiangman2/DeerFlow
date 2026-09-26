"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { resourceRequest } from "@/core/agents/resources";
import { useI18n } from "@/core/i18n/hooks";

interface Proposal {
  id: string;
  name: string;
  state: "pending" | "applied" | "rejected";
  expected_draft_revision: string;
  current_draft_revision: string;
  conflict: boolean;
  before: Record<string, unknown>;
  updates: Record<string, unknown>;
  before_soul: string | null;
  soul: string | null;
}

export function EmployeeChangeReview({
  name,
  id,
}: {
  name: string;
  id: string;
}) {
  const { locale } = useI18n();
  const zh = locale === "zh-CN";
  const [confirmed, setConfirmed] = useState(false);
  const path = `agents/${encodeURIComponent(name)}/proposals/${encodeURIComponent(id)}`;
  const query = useQuery({
    queryKey: ["employee-proposal", name, id],
    queryFn: () => resourceRequest<Proposal>(path),
    retry: false,
  });
  const decision = useMutation({
    mutationFn: (approve: boolean) =>
      resourceRequest(`${path}/decision`, {
        approve,
        expected_draft_revision: query.data?.expected_draft_revision,
      }),
    onSuccess: () => {
      setConfirmed(false);
      void query.refetch();
    },
    retry: false,
  });
  const proposal = query.data;
  const error = decision.error ?? query.error;
  const busy = decision.isPending || query.isFetching;
  return (
    <section className="mx-auto flex w-full max-w-4xl flex-col gap-5 overflow-y-auto p-6">
      <h1 className="text-xl font-semibold">
        {zh ? "审核数字员工修改建议" : "Review employee change proposal"}
      </h1>
      <p className="text-muted-foreground text-sm">
        {zh
          ? "模型只提交建议。确认后修改个人草稿，不会自动发布到广场，也不会删除员工、头像或记忆。"
          : "The model only proposes changes. Confirmation updates your private draft, not the Square release, and preserves employee identity, uploaded avatar and memory."}
      </p>
      {query.isLoading && (
        <p role="status">{zh ? "加载建议中…" : "Loading proposal…"}</p>
      )}
      {error && (
        <p role="alert" className="text-destructive">
          {error.message}
        </p>
      )}
      {proposal && (
        <>
          <p>
            {name} · {proposal.state}
          </p>
          {proposal.conflict && (
            <p role="alert" className="text-destructive">
              {zh
                ? "草稿已被修改，此建议不能覆盖新内容。请基于当前草稿重新提出建议。"
                : "The draft has changed. This proposal cannot overwrite newer edits; request a new proposal based on the current draft."}
            </p>
          )}
          <div className="grid gap-4 md:grid-cols-2">
            <section className="min-w-0 rounded-lg border p-4">
              <h2 className="mb-3 font-medium">{zh ? "修改前" : "Before"}</h2>
              <pre className="text-sm break-all whitespace-pre-wrap">
                {JSON.stringify(proposal.before, null, 2)}
              </pre>
              {proposal.before_soul !== null && (
                <pre className="mt-4 text-sm break-all whitespace-pre-wrap">
                  {proposal.before_soul}
                </pre>
              )}
            </section>
            <section className="min-w-0 rounded-lg border p-4">
              <h2 className="mb-3 font-medium">
                {zh ? "建议修改为" : "Proposed"}
              </h2>
              <pre className="text-sm break-all whitespace-pre-wrap">
                {JSON.stringify(proposal.updates, null, 2)}
              </pre>
              {proposal.soul !== null && (
                <pre className="mt-4 text-sm break-all whitespace-pre-wrap">
                  {proposal.soul}
                </pre>
              )}
            </section>
          </div>
          {proposal.state === "pending" && (
            <>
              <label className="flex gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4 accent-blue-600"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                  disabled={busy || proposal.conflict}
                />
                {zh
                  ? "我已审核这些修改，同意保存到个人草稿。"
                  : "I have reviewed these changes and approve saving them to my private draft."}
              </label>
              <div className="flex gap-3">
                <Button
                  disabled={
                    !confirmed || proposal.conflict || busy || !!decision.error
                  }
                  onClick={() => decision.mutate(true)}
                >
                  {zh ? "确认修改草稿" : "Confirm draft changes"}
                </Button>
                <Button
                  variant="outline"
                  disabled={busy || !!decision.error}
                  onClick={() => decision.mutate(false)}
                >
                  {zh ? "拒绝建议" : "Reject proposal"}
                </Button>
              </div>
            </>
          )}
        </>
      )}
      <Button asChild variant="outline">
        <Link href={`/workspace/agents/manage/${encodeURIComponent(name)}`}>
          {zh ? "查看版本与管理" : "Versions and management"}
        </Link>
      </Button>
    </section>
  );
}
