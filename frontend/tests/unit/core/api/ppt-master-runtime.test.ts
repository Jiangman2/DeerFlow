import { describe, expect, it, rs } from "@rstest/core";

import {
  PPT_MASTER_RUNTIME_ASSISTANT_ID,
  hasPptMasterRuntimeMetadata,
  inheritPptMasterRuntimeThread,
  isExplicitPptMasterRunInput,
  rememberPptMasterRuntimeThread,
  shouldUsePptMasterRuntime,
} from "@/core/api/ppt-master-runtime";

describe("ppt-master isolated runtime routing", () => {
  it("routes only an explicit leading slash activation", () => {
    expect(
      isExplicitPptMasterRunInput({
        messages: [{ role: "user", content: "/ppt-master 请创建5页PPT" }],
      }),
    ).toBe(true);
    expect(
      isExplicitPptMasterRunInput({
        messages: [{ role: "user", content: "请介绍 ppt-master" }],
      }),
    ).toBe(false);
  });

  it("supports SDK text content blocks and ignores earlier activations", () => {
    expect(
      isExplicitPptMasterRunInput({
        messages: [
          { role: "user", content: "/ppt-master old" },
          { role: "assistant", content: "done" },
          { role: "user", content: [{ type: "text", text: "继续" }] },
        ],
      }),
    ).toBe(false);
  });

  it("recognizes persisted runner thread metadata", () => {
    expect(
      hasPptMasterRuntimeMetadata({
        metadata: { runtime_profile: PPT_MASTER_RUNTIME_ASSISTANT_ID },
      }),
    ).toBe(true);
    expect(hasPptMasterRuntimeMetadata({ metadata: {} })).toBe(false);
  });

  it("marks an explicit first submit before state hydration starts", () => {
    const setItem = rs.fn();
    rs.stubGlobal("window", { sessionStorage: { setItem } });

    expect(
      rememberPptMasterRuntimeThread("thread-ppt", {
        messages: [{ role: "user", content: "/ppt-master 创建演示文稿" }],
      }),
    ).toBe(true);
    expect(setItem).toHaveBeenCalledWith(
      "deerflow:ppt-master-runtime:thread-ppt",
      PPT_MASTER_RUNTIME_ASSISTANT_ID,
    );
  });

  it("inherits the runtime marker when the runner returns a different id", () => {
    const values = new Map<string, string>();
    const sessionStorage = {
      getItem: rs.fn((key: string) => values.get(key) ?? null),
      setItem: rs.fn((key: string, value: string) => values.set(key, value)),
    };
    rs.stubGlobal("window", { sessionStorage });

    rememberPptMasterRuntimeThread("welcome-thread", {
      messages: [{ role: "user", content: "/ppt-master 创建演示文稿" }],
    });

    expect(
      inheritPptMasterRuntimeThread("welcome-thread", "runner-thread"),
    ).toBe(true);
    expect(values.get("deerflow:ppt-master-runtime:runner-thread")).toBe(
      PPT_MASTER_RUNTIME_ASSISTANT_ID,
    );
  });

  it("keeps routing in memory when sessionStorage is unavailable", () => {
    rs.stubGlobal("window", {
      get sessionStorage() {
        throw new DOMException("Blocked", "SecurityError");
      },
    });

    expect(
      rememberPptMasterRuntimeThread("storage-blocked-source", {
        messages: [{ role: "user", content: "/ppt-master 创建演示文稿" }],
      }),
    ).toBe(true);
    expect(
      inheritPptMasterRuntimeThread(
        "storage-blocked-source",
        "storage-blocked-target",
      ),
    ).toBe(true);
  });

  it("does not probe runs before an ordinary first submit creates the thread", async () => {
    const list = rs.fn();
    const client = { runs: { list } };

    await expect(
      shouldUsePptMasterRuntime(
        client as unknown as Parameters<typeof shouldUsePptMasterRuntime>[0],
        "ordinary-first-submit",
        {
          messages: [{ role: "user", content: "分析这份资料" }],
        },
      ),
    ).resolves.toBe(false);
    expect(list).not.toHaveBeenCalled();
  });
});
