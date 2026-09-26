"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  resourceLabel,
  resourceRequest,
  type EmployeeGrant,
} from "@/core/agents/resources";
import { useI18n } from "@/core/i18n/hooks";

interface GrantDetail {
  grant: EmployeeGrant;
  can_decide: boolean;
  can_revoke: boolean;
}

export function ResourceGrantDetail({ id }: { id: string }) {
  const { locale } = useI18n();
  const zh = locale === "zh-CN";
  const [acknowledged, setAcknowledged] = useState(false);
  const [days, setDays] = useState("30");
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const [copied, setCopied] = useState(false);
  const query = useQuery({
    queryKey: ["employee-resource-grant", id],
    queryFn: () =>
      resourceRequest<GrantDetail>(
        `employee-resource-grants/${encodeURIComponent(id)}`,
      ),
    retry: false,
  });
  const change = useMutation({
    mutationFn: (decision: "approve" | "reject" | "revoke") => {
      if (!query.data) throw new Error("Reload authorization before deciding");
      const common = { expected_revision: query.data.grant.revision };
      return resourceRequest(
        decision === "revoke"
          ? `employee-resource-grants/${id}/revoke`
          : `employee-resource-grants/${id}/decision`,
        decision === "revoke"
          ? common
          : {
              ...common,
              approve: decision === "approve",
              acknowledge_onward_sharing: acknowledged,
              expires_at: new Date(
                Date.now() + Number(days) * 86_400_000,
              ).toISOString(),
            },
      );
    },
    onSuccess: () => {
      setAcknowledged(false);
      setConfirmRevoke(false);
      void query.refetch();
    },
    retry: false,
  });
  const error = change.error ?? query.error;
  const grant = query.data?.grant;
  const pending = query.isFetching || change.isPending;
  const validDays =
    Number.isInteger(Number(days)) && Number(days) >= 1 && Number(days) <= 365;
  return (
    <section className="mx-auto flex w-full max-w-2xl flex-col gap-5 overflow-y-auto p-6">
      <h1 className="text-xl font-semibold">
        {zh ? "数字员工资源调用授权" : "Employee resource invocation approval"}
      </h1>
      <p className="text-muted-foreground text-sm">
        {zh
          ? "授权仅对指定数字员工生效，供全体已登录广场用户调用；知识库不开放原文浏览或下载，Skill 不提供源码入口。"
          : "Approval applies to this employee for all signed-in Square users. Knowledge is Q&A-only; Skill source browsing and download are not provided."}
      </p>
      {query.isLoading && (
        <p role="status">{zh ? "核验权限中…" : "Checking permissions…"}</p>
      )}
      {error && (
        <p role="alert" className="text-destructive">
          {error.message}
        </p>
      )}
      {grant && (
        <>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-lg border p-4 text-sm">
            <dt>{zh ? "资源" : "Resource"}</dt>
            <dd className="break-all">{resourceLabel(grant.resource)}</dd>
            <dt>{zh ? "员工标识" : "Employee ID"}</dt>
            <dd className="break-all">{grant.publication_id}</dd>
            <dt>{zh ? "申请人" : "Publisher"}</dt>
            <dd className="break-all">{grant.publisher}</dd>
            <dt>{zh ? "状态" : "Status"}</dt>
            <dd>
              {grant.state}
              {grant.expires_at && Date.parse(grant.expires_at) <= Date.now()
                ? zh
                  ? "（已过期）"
                  : " (expired)"
                : ""}
            </dd>
            {grant.expires_at && (
              <>
                <dt>{zh ? "有效期至" : "Expires"}</dt>
                <dd>{new Date(grant.expires_at).toLocaleString()}</dd>
              </>
            )}
          </dl>
          <Button
            variant="outline"
            onClick={() => {
              void navigator.clipboard
                .writeText(window.location.href)
                .then(() => setCopied(true))
                .catch(() => setCopied(false));
            }}
          >
            {copied
              ? zh
                ? "已复制审批链接"
                : "Approval link copied"
              : zh
                ? "复制审批链接"
                : "Copy approval link"}
          </Button>
          {query.data?.can_decide && (
            <>
              <p className="text-sm">
                {grant.resource.kind === "skill"
                  ? zh
                    ? "管理员必须先确认组织拥有该 Skill 的共享调用权。已安装不等于有转授权权利。"
                    : "Administrators must verify organizational onward-sharing rights. Installation alone is not authorization."
                  : zh
                    ? "仅知识库当前所有者可以审批；只读共享权限不能转授权。"
                    : "Only the current knowledge owner may approve; read-only access does not permit onward sharing."}
              </p>
              <label htmlFor="grant-days" className="text-sm font-medium">
                {zh ? "授权天数（1–365）" : "Approval duration (1–365 days)"}
              </label>
              <Input
                id="grant-days"
                type="number"
                min="1"
                max="365"
                value={days}
                onChange={(event) => setDays(event.target.value)}
                disabled={pending}
              />
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1 size-4 shrink-0 accent-blue-600"
                  checked={acknowledged}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                  disabled={pending}
                />
                <span>
                  {zh
                    ? "我确认有权授权上述资源给该数字员工，并了解广场使用范围。"
                    : "I confirm my authority to grant this resource and the Square-wide invocation scope."}
                </span>
              </label>
              <div className="flex gap-3">
                <Button
                  disabled={
                    !acknowledged || !validDays || pending || !!change.error
                  }
                  onClick={() => change.mutate("approve")}
                >
                  {zh ? "批准授权" : "Approve"}
                </Button>
                <Button
                  variant="outline"
                  disabled={!validDays || pending || !!change.error}
                  onClick={() => change.mutate("reject")}
                >
                  {zh ? "拒绝" : "Reject"}
                </Button>
              </div>
            </>
          )}
          {query.data?.can_revoke && (
            <div className="space-y-2 rounded-lg border p-4">
              <p className="text-sm">
                {zh
                  ? "撤销会阻止该员工后续使用此资源，不删除员工或对话。已经返回的答案无法收回。"
                  : "Revocation blocks subsequent resource use without deleting the employee or conversations. Previously returned answers cannot be recalled."}
              </p>
              {!confirmRevoke ? (
                <Button
                  variant="outline"
                  disabled={pending}
                  onClick={() => setConfirmRevoke(true)}
                >
                  {zh ? "撤销授权" : "Revoke approval"}
                </Button>
              ) : (
                <Button
                  variant="destructive"
                  disabled={pending}
                  onClick={() => change.mutate("revoke")}
                >
                  {zh ? "确认撤销" : "Confirm revocation"}
                </Button>
              )}
            </div>
          )}
        </>
      )}
      <Button
        variant="outline"
        disabled={pending}
        onClick={() => {
          change.reset();
          setAcknowledged(false);
          void query.refetch();
        }}
      >
        {zh ? "刷新" : "Refresh"}
      </Button>
    </section>
  );
}
