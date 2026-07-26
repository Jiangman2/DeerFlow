import type { Model } from "@/core/models/types";

export const DEFAULT_SCHEDULED_MODEL_NAME = "deepseek-v4-flash";

export function resolveScheduledModelName(
  models: Model[],
  requestedModelName?: string,
): string {
  if (
    requestedModelName &&
    models.some((model) => model.name === requestedModelName)
  ) {
    return requestedModelName;
  }
  return (
    models.find((model) => model.name === DEFAULT_SCHEDULED_MODEL_NAME)?.name ??
    models[0]?.name ??
    ""
  );
}
