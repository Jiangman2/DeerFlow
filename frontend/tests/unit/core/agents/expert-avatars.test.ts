import { expect, test } from "@rstest/core";

import {
  EXPERT_AVATAR_OPTIONS,
  getDefaultExpertAvatar,
  getExpertAvatarById,
} from "@/core/agents/expert-avatars";

test("maps known external experts to their role portrait", () => {
  expect(
    getDefaultExpertAvatar({
      name: "地震数据提频专家",
      external_url: "https://datalake.cnooc/dzsjtpzj",
    }).id,
  ).toBe("seismic-processing");
});

test("maps role keywords when an external URL is absent", () => {
  expect(getDefaultExpertAvatar({ name: "钻完井方案专家" }).id).toBe(
    "drilling-completion",
  );
  expect(getDefaultExpertAvatar({ name: "HSE 审查专家" }).id).toBe("hse");
});

test("uses a stable available portrait for arbitrary agent names", () => {
  const first = getDefaultExpertAvatar({ name: "monthly-report-expert" });
  const second = getDefaultExpertAvatar({ name: "monthly-report-expert" });

  expect(first).toEqual(second);
  expect(EXPERT_AVATAR_OPTIONS).toContain(first);
  expect(getExpertAvatarById(first.id)).toEqual(first);
});

test("ships a rich cartoon avatar set with circular-crop assets", () => {
  expect(EXPERT_AVATAR_OPTIONS).toHaveLength(24);
  expect(
    EXPERT_AVATAR_OPTIONS.every((option) =>
      option.src.startsWith("/images/digital-employees/cartoon/"),
    ),
  ).toBe(true);
  expect(
    EXPERT_AVATAR_OPTIONS.some((option) => option.id === "emergency-response"),
  ).toBe(true);
});
