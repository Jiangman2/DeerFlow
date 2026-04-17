import type { UserMemory } from "./types";

export async function loadMemory() {
  const res = await fetch("/api/memory", { cache: "no-store" });
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      detail = "";
    }
    const msg = detail ? `${res.status} ${detail}` : `${res.status}`;
    throw new Error(msg);
  }
  const json = (await res.json()) as unknown;
  return json as UserMemory;
}
