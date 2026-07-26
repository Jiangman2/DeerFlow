import { describe, expect, it } from "@rstest/core";

import type { Model } from "@/core/models/types";
import {
  DEFAULT_SCHEDULED_MODEL_NAME,
  resolveScheduledModelName,
} from "@/core/scheduled-tasks/model-selection";

const MODELS: Model[] = [
  {
    id: "qwen",
    name: "qwen-bailian",
    model: "qwen3.7-plus",
    display_name: "Qwen 3.7 Plus",
  },
  {
    id: "deepseek",
    name: "deepseek-v4-flash",
    model: "DeepSeek-V4-Flash",
    display_name: "DeepSeek V4 Flash",
  },
];

describe("scheduled task model selection", () => {
  it("defaults enterprise scheduled tasks to deepseek-v4-flash", () => {
    expect(DEFAULT_SCHEDULED_MODEL_NAME).toBe("deepseek-v4-flash");
    expect(resolveScheduledModelName(MODELS)).toBe("deepseek-v4-flash");
  });

  it("preserves a valid task-specific model selection", () => {
    expect(resolveScheduledModelName(MODELS, "qwen-bailian")).toBe(
      "qwen-bailian",
    );
  });

  it("falls back to the first available model when DeepSeek is unavailable", () => {
    expect(resolveScheduledModelName(MODELS.slice(0, 1))).toBe(
      "qwen-bailian",
    );
  });

  it("returns an empty selection while no models are available", () => {
    expect(resolveScheduledModelName([])).toBe("");
  });
});
