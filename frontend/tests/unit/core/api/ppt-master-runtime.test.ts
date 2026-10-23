import { afterEach, describe, expect, it, rs } from "@rstest/core";

import {
  PPT_MASTER_RUNTIME_ASSISTANT_ID,
  hasPptMasterRuntimeMetadata,
  inheritPptMasterRuntimeThread,
  rememberPptMasterRuntimeThread,
  shouldUsePptMasterRuntime,
} from "@/core/api/ppt-master-runtime";

afterEach(() => {
  rs.unstubAllGlobals();
});

describe("persisted ppt-master runtime ownership", () => {
  it("recognizes persisted runner thread metadata", () => {
    expect(
      hasPptMasterRuntimeMetadata({
        metadata: { runtime_profile: PPT_MASTER_RUNTIME_ASSISTANT_ID },
      }),
    ).toBe(true);
    expect(hasPptMasterRuntimeMetadata({ metadata: {} })).toBe(false);
  });

  it("does not mark a new Slash Skill request as a runner thread", () => {
    const setItem = rs.fn();
    rs.stubGlobal("window", { sessionStorage: { setItem } });

    expect(
      rememberPptMasterRuntimeThread("thread-ppt", {
        messages: [{ role: "user", content: "/ppt-master 创建演示文稿" }],
      }),
    ).toBe(false);
    expect(setItem).not.toHaveBeenCalled();
  });

  it("inherits the runtime marker when the runner returns a different id", () => {
    const values = new Map<string, string>();
    const sessionStorage = {
      getItem: rs.fn((key: string) => values.get(key) ?? null),
      setItem: rs.fn((key: string, value: string) => values.set(key, value)),
    };
    rs.stubGlobal("window", { sessionStorage });

    rememberPptMasterRuntimeThread("welcome-thread", {
      metadata: { runtime_profile: PPT_MASTER_RUNTIME_ASSISTANT_ID },
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
        metadata: { runtime_profile: PPT_MASTER_RUNTIME_ASSISTANT_ID },
      }),
    ).toBe(true);
    expect(
      inheritPptMasterRuntimeThread(
        "storage-blocked-source",
        "storage-blocked-target",
      ),
    ).toBe(true);
  });

  it("keeps a created thread with no runner history on the ordinary Agent", async () => {
    const list = rs.fn(async () => []);
    const client = { runs: { list } };

    await expect(
      shouldUsePptMasterRuntime(
        client as unknown as Parameters<typeof shouldUsePptMasterRuntime>[0],
        "ordinary-first-submit",
        { submitting: true },
      ),
    ).resolves.toBe(false);
    expect(list).toHaveBeenCalledWith("ordinary-first-submit");
  });

  it("recovers runner ownership for an early continuation with blocked storage", async () => {
    rs.stubGlobal("window", {
      get sessionStorage() {
        throw new DOMException("Blocked", "SecurityError");
      },
    });
    const list = rs.fn(async () => [
      { assistant_id: PPT_MASTER_RUNTIME_ASSISTANT_ID },
    ]);
    const client = { runs: { list } } as unknown as Parameters<
      typeof shouldUsePptMasterRuntime
    >[0];
    await expect(
      shouldUsePptMasterRuntime(client, "early-runner-continuation", {
        submitting: true,
      }),
    ).resolves.toBe(true);
    await expect(
      shouldUsePptMasterRuntime(client, "early-runner-continuation"),
    ).resolves.toBe(true);
    expect(list).toHaveBeenCalledTimes(1);
  });

  it("does not submit to a different owner when run discovery fails", async () => {
    const list = rs.fn(async () => {
      throw new Error("owner lookup unavailable");
    });
    const client = { runs: { list } } as unknown as Parameters<
      typeof shouldUsePptMasterRuntime
    >[0];
    await expect(
      shouldUsePptMasterRuntime(client, "unknown-owner-submit", {
        submitting: true,
      }),
    ).rejects.toThrow("owner lookup unavailable");
  });
});
