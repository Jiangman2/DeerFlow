import { describe, expect, it } from "@rstest/core";

import {
  PPT_MASTER_RUNTIME_ASSISTANT_ID,
  hasPptMasterRuntimeMetadata,
  isExplicitPptMasterRunInput,
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
});
