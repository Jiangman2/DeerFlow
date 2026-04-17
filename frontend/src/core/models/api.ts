import type { Model } from "./types";

export async function loadModels() {
  const res = await fetch("/api/models");
  const { models } = (await res.json()) as { models: Model[] };
  return models;
}
