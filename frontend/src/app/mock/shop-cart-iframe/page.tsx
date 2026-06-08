"use client";

import { FileTextIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { SHOP_CART_MOCK_FILES } from "@/core/shop-cart/mock-files";
import { cn } from "@/lib/utils";

const scenarioLabels = {
  "neighbor-well": {
    title: "场景一：调整井邻井/相似井深度研究",
    desc: "用于展示 Skill 触发、资料处理、邻井筛选、风险矩阵和方案建议。",
  },
  "production-decline": {
    title: "场景二：生产动态递减分析与产量预测",
    desc: "用于展示结构化数据分析、固定工具链、递减拟合和预测成果生成。",
  },
  "project-ppt": {
    title: "场景三：立项报告智能汇报PPT生成",
    desc: "用于展示基于项目立项报告的长链路资料解析、PPT规划、素材生成、质量检查和成果导出。",
  },
} as const;

export default function MockShopCartIframePage() {
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const selectedIdSet = useMemo(() => new Set(selectedIds), [selectedIds]);

  const postSelectedIds = useCallback(() => {
    const targetWindow = window.opener ?? window.parent;
    const payload = {
      type: "shopCartSelectedIds",
      data: selectedIds,
    };
    window.parent.postMessage(payload, window.location.origin);
    if (targetWindow !== window.parent) {
      targetWindow.postMessage(payload, window.location.origin);
    }
  }, [selectedIds]);

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) {
        return;
      }
      if (event.data?.type !== "getShopCartSelectedIds") {
        return;
      }
      postSelectedIds();
    };

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [postSelectedIds]);

  const toggleFile = useCallback((id: string) => {
    setSelectedIds((current) =>
      current.includes(id)
        ? current.filter((selectedId) => selectedId !== id)
        : [...current, id],
    );
  }, []);

  const groups = useMemo(
    () =>
      (["neighbor-well", "production-decline", "project-ppt"] as const).map((scenario) => ({
        scenario,
        files: SHOP_CART_MOCK_FILES.filter((file) => file.scenario === scenario),
      })),
    [],
  );

  return (
    <main className="min-h-screen bg-[#f7f9fc] p-4 text-[#152238]">
      <div className="mb-4">
        <h1 className="text-lg font-semibold">资料车</h1>
        <p className="mt-1 text-xs text-[#66748a]">
          预置三个场景的演示资料包，可按现场演示重点选择需要加入对话的文件。
        </p>
      </div>

      <div className="space-y-4">
        {groups.map(({ scenario, files }) => (
          <section key={scenario} className="space-y-2">
            <div>
              <h2 className="text-sm font-semibold">
                {scenarioLabels[scenario].title}
              </h2>
              <p className="mt-0.5 text-xs text-[#66748a]">
                {scenarioLabels[scenario].desc}
              </p>
            </div>

            <div className="space-y-2">
              {files.map((file) => {
                const checked = selectedIdSet.has(file.id);
                return (
                  <label
                    key={file.id}
                    className={cn(
                      "flex cursor-pointer items-center gap-3 rounded-lg border bg-white p-3 shadow-xs transition",
                      checked
                        ? "border-[#82a8ff] ring-2 ring-[#d8e5ff]"
                        : "border-[#d8e0eb]",
                    )}
                  >
                    <input
                      checked={checked}
                      className="size-4 accent-[#1f5eff]"
                      type="checkbox"
                      onChange={() => toggleFile(file.id)}
                    />
                    <FileTextIcon className="size-5 shrink-0 text-[#3d5f91]" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {file.filename}
                      </span>
                      <span className="block text-xs text-[#718096]">
                        {file.id}
                      </span>
                      <span className="mt-1 block text-xs text-[#4f5f76]">
                        {file.description}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
