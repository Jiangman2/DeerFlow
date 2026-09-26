"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
import { agentChatPath, useDigitalEmployeeCategories } from "@/core/agents";
import { AgentPublicationError, getAgent } from "@/core/agents/api";
import { resourceRequest } from "@/core/agents/resources";
import { useI18n } from "@/core/i18n/hooks";

import { DigitalEmployeeSettingsDialog } from "./digital-employee-settings-dialog";
import { PublicationReviewDialog } from "./publication-review-dialog";

interface Version {
  revision: string;
  state: "published" | "withdrawn";
  created_at: string;
}

export function EmployeeVersions({ name }: { name: string }) {
  const { locale } = useI18n();
  const zh = locale === "zh-CN";
  const cache = useQueryClient();
  const { categories } = useDigitalEmployeeCategories();
  const [edit, setEdit] = useState(false);
  const [publish, setPublish] = useState(false);
  const [selected, setSelected] = useState<Version | null>(null);
  const draft = useQuery({
    queryKey: ["employee-managed-draft", name],
    queryFn: () => getAgent(name, "personal"),
    retry: false,
  });
  const history = useQuery({
    queryKey: ["employee-publications", name],
    queryFn: async () => {
      try {
        return await resourceRequest<{ revisions: Version[] }>(
          `agents/${encodeURIComponent(name)}/publications`,
        );
      } catch (error) {
        if (error instanceof AgentPublicationError && error.status === 404)
          return { revisions: [] };
        throw error;
      }
    },
    retry: false,
  });
  const proposals = useQuery({
    queryKey: ["employee-proposals", name],
    queryFn: () =>
      resourceRequest<{
        proposals: { id: string; state: string; created_at: string }[];
      }>(`agents/${encodeURIComponent(name)}/proposals`),
    retry: false,
  });
  const restore = useMutation({
    mutationFn: (version: Version) =>
      resourceRequest(
        `agents/${encodeURIComponent(name)}/publications/${version.revision}/restore-draft`,
        { expected_draft_revision: draft.data?.draft_revision },
      ),
    onSuccess: () => {
      setSelected(null);
      void draft.refetch();
      void cache.invalidateQueries({ queryKey: ["agents"] });
    },
    retry: false,
  });
  const error =
    restore.error ?? draft.error ?? history.error ?? proposals.error;
  return (
    <section className="mx-auto flex w-full max-w-3xl flex-col gap-5 overflow-y-auto p-6">
      <h1 className="text-xl font-semibold">
        {zh ? "数字员工版本与维护" : "Employee versions and maintenance"}
      </h1>
      <p className="text-muted-foreground text-sm">
        {zh
          ? "草稿编辑、广场发布和历史会话版本相互独立。新发布用于新会话；已有会话保持原版本。下架或撤销资源授权会阻止后续调用。"
          : "Drafts, Square releases and existing conversations are separate. New conversations use the new release; existing conversations stay pinned. Withdrawal or revoked grants block subsequent use."}
      </p>
      {error && (
        <p role="alert" className="text-destructive">
          {error.message}
        </p>
      )}
      {draft.data && (
        <>
          <h2 className="font-semibold">{draft.data.display_name ?? name}</h2>
          <div className="flex flex-wrap gap-3">
            <Button onClick={() => setEdit(true)}>
              {zh ? "编辑草稿" : "Edit draft"}
            </Button>
            <Button variant="outline" asChild>
              <Link href={agentChatPath(name, "new", "personal")}>
                {zh ? "通过对话提出修改" : "Propose edits in chat"}
              </Link>
            </Button>
            <Button variant="outline" onClick={() => setPublish(true)}>
              {zh ? "审核并发布" : "Review and publish"}
            </Button>
          </div>
          <DigitalEmployeeSettingsDialog
            agent={draft.data}
            categories={categories}
            open={edit}
            onOpenChange={(value) => {
              setEdit(value);
              if (!value) void draft.refetch();
            }}
          />
        </>
      )}
      {publish && (
        <PublicationReviewDialog
          name={name}
          onClose={() => {
            setPublish(false);
            void history.refetch();
            void draft.refetch();
          }}
        />
      )}
      <h2 className="font-semibold">
        {zh ? "对话修改建议" : "Conversational change proposals"}
      </h2>
      <ul className="space-y-2">
        {proposals.data?.proposals.map((proposal) => (
          <li key={proposal.id} className="rounded-lg border p-3 text-sm">
            <Link
              className="text-primary underline"
              href={`/workspace/agents/manage/${encodeURIComponent(name)}/proposals/${proposal.id}`}
            >
              {zh ? "查看并审核" : "Open review"} · {proposal.state} ·{" "}
              {new Date(proposal.created_at).toLocaleString()}
            </Link>
          </li>
        ))}
      </ul>
      {proposals.data?.proposals.length === 0 && (
        <p className="text-muted-foreground text-sm">
          {zh ? "暂无修改建议。" : "No change proposals yet."}
        </p>
      )}
      <h2 className="font-semibold">
        {zh ? "已提交的发布历史" : "Committed publication history"}
      </h2>
      {(draft.isLoading || history.isLoading) && (
        <p role="status">{zh ? "加载中…" : "Loading…"}</p>
      )}
      {history.data?.revisions.length === 0 && (
        <p>
          {zh
            ? "暂无发布记录，个人员工仍保留。"
            : "No releases yet. The personal employee is preserved."}
        </p>
      )}
      <ul className="space-y-3">
        {history.data?.revisions.map((version) => (
          <li
            key={version.revision}
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4"
          >
            <div className="text-sm">
              <p>
                {new Date(version.created_at).toLocaleString()} ·{" "}
                {version.state}
              </p>
              <p className="text-muted-foreground">
                {version.revision.slice(0, 12)}
              </p>
            </div>
            {version.state === "published" && (
              <Button
                variant="outline"
                disabled={!draft.data || restore.isPending}
                onClick={() => {
                  restore.reset();
                  setSelected(version);
                }}
              >
                {zh ? "恢复到草稿…" : "Restore to draft…"}
              </Button>
            )}
          </li>
        ))}
      </ul>
      <Dialog
        open={!!selected}
        onOpenChange={(open) => {
          if (!open && !restore.isPending) setSelected(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {zh ? "确认恢复历史定义" : "Restore historical definition?"}
            </DialogTitle>
            <DialogDescription>
              {zh
                ? "会替换当前草稿的受管理字段和 SOUL，保留 ID、未知配置、记忆和上传头像；不会修改广场发布。资源权限会重新核验。"
                : "Replace managed draft fields and SOUL while retaining ID, unknown settings, memory and uploaded avatar. The Square release is unchanged; resource permissions are rechecked."}
            </DialogDescription>
          </DialogHeader>
          {restore.error && (
            <p role="alert" className="text-destructive">
              {restore.error.message}
            </p>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={restore.isPending}
              onClick={() => setSelected(null)}
            >
              {zh ? "取消" : "Cancel"}
            </Button>
            <Button
              disabled={!selected || restore.isPending || !!restore.error}
              onClick={() => selected && restore.mutate(selected)}
            >
              {zh ? "确认恢复" : "Confirm restore"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
