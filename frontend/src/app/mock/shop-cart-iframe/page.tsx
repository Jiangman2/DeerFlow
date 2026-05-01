"use client";

import { FileTextIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { SHOP_CART_MOCK_FILES } from "@/core/shop-cart/mock-files";
import { cn } from "@/lib/utils";

const initialSelectedIds = ["file-001", "file-002"];

export default function MockShopCartIframePage() {
  const [selectedIds, setSelectedIds] = useState<string[]>(initialSelectedIds);

  const selectedIdSet = useMemo(() => new Set(selectedIds), [selectedIds]);

  const postSelectedIds = useCallback(() => {
    const targetWindow = window.opener ?? window.parent;
    window.parent.postMessage(
      {
        type: "shopCartSelectedIds",
        data: selectedIds,
      },
      window.location.origin,
    );
    if (targetWindow !== window.parent) {
      targetWindow.postMessage(
        {
          type: "shopCartSelectedIds",
          data: selectedIds,
        },
        window.location.origin,
      );
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

  return (
    <main className="min-h-screen bg-[#f7f9fc] p-4 text-[#152238]">
      <div className="mb-4">
        <div>
          <h1 className="text-lg font-semibold">资料车</h1>
          <p className="mt-1 text-xs text-[#66748a]">
            本地 mock iframe，用于验证附件选择流程。勾选文件后，在父页面点击添加。
          </p>
        </div>
      </div>

      <div className="space-y-2">
        {SHOP_CART_MOCK_FILES.map((file) => {
          const checked = selectedIdSet.has(file.id);
          return (
            <label
              key={file.id}
              className={cn(
                "flex cursor-pointer items-center gap-3 rounded-lg border bg-white p-3 shadow-xs transition",
                checked ? "border-[#82a8ff] ring-2 ring-[#d8e5ff]" : "border-[#d8e0eb]",
              )}
            >
              <input
                checked={checked}
                className="size-4 accent-[#1f5eff]"
                type="checkbox"
                onChange={() => toggleFile(file.id)}
              />
              <FileTextIcon className="size-5 text-[#3d5f91]" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">
                  {file.filename}
                </span>
                <span className="block text-xs text-[#718096]">{file.id}</span>
              </span>
            </label>
          );
        })}
      </div>
    </main>
  );
}
