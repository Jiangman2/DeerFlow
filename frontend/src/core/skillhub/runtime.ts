import { fetch as fetchWithAuth } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import { SKILLHUB_BASE_PATH } from "./embed";

export type SkillHubSkillLocation = {
  namespace: string;
  slug: string;
};

export type SkillHubDetail = SkillHubSkillLocation & {
  displayName: string;
  summary: string;
  version: string;
};

export type SkillHubRuntimeSkill = SkillHubSkillLocation & {
  version: string;
  skill_name: string;
  enabled: boolean;
  installed: boolean;
  sha256: string;
  installed_at: string;
  updated_at: string;
};

export type SkillHubRuntimeInstallResult = SkillHubRuntimeSkill & {
  action: "installed" | "upgraded" | "already_current";
};

type SkillHubDetailEnvelope = {
  data?: {
    namespace?: unknown;
    slug?: unknown;
    displayName?: unknown;
    summary?: unknown;
    publishedVersion?: { version?: unknown } | null;
    headlineVersion?: { version?: unknown } | null;
  } | null;
};

const SKILL_DETAIL_PREFIX = `${SKILLHUB_BASE_PATH}/space/`;
const SAFE_IDENTITY_PART = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function readSkillHubSkillLocation(
  pathname: string,
): SkillHubSkillLocation | null {
  const cleanPath = pathname.split(/[?#]/, 1)[0] ?? "";
  if (!cleanPath.startsWith(SKILL_DETAIL_PREFIX)) return null;
  const parts = cleanPath.slice(SKILL_DETAIL_PREFIX.length).split("/");
  if (parts.length !== 2) return null;
  try {
    const namespace = decodeURIComponent(parts[0] ?? "")
      .replace(/^@/, "")
      .toLowerCase();
    const slug = decodeURIComponent(parts[1] ?? "").toLowerCase();
    if (!SAFE_IDENTITY_PART.test(namespace) || !SAFE_IDENTITY_PART.test(slug)) {
      return null;
    }
    return { namespace, slug };
  } catch {
    return null;
  }
}

export function readSkillHubDetail(payload: unknown): SkillHubDetail | null {
  const data = (payload as SkillHubDetailEnvelope | null)?.data;
  const version =
    data?.publishedVersion?.version ?? data?.headlineVersion?.version;
  if (
    !data ||
    typeof data.namespace !== "string" ||
    typeof data.slug !== "string" ||
    typeof version !== "string"
  ) {
    return null;
  }
  return {
    namespace: data.namespace.replace(/^@/, ""),
    slug: data.slug,
    displayName:
      typeof data.displayName === "string" && data.displayName.trim()
        ? data.displayName
        : data.slug,
    summary: typeof data.summary === "string" ? data.summary : "",
    version,
  };
}

function skillHubApiIdentityPath(namespace: string, slug: string) {
  return `${encodeURIComponent(namespace.replace(/^@/, ""))}/${encodeURIComponent(slug)}`;
}

export function skillHubDetailPath(namespace: string, slug: string) {
  return `${SKILLHUB_BASE_PATH}/space/@${skillHubApiIdentityPath(namespace, slug)}`;
}

export function skillHubDownloadPath(
  namespace: string,
  slug: string,
  version: string,
) {
  return `${SKILLHUB_BASE_PATH}/api/web/skills/${skillHubApiIdentityPath(namespace, slug)}/versions/${encodeURIComponent(version)}/download`;
}

async function readError(response: Response): Promise<string> {
  const body = (await response.json().catch(() => null)) as {
    detail?: unknown;
    msg?: unknown;
  } | null;
  if (typeof body?.detail === "string") return body.detail;
  if (
    body?.detail &&
    typeof body.detail === "object" &&
    "message" in body.detail &&
    typeof body.detail.message === "string"
  ) {
    return body.detail.message;
  }
  if (typeof body?.msg === "string") return body.msg;
  return `HTTP ${response.status}: ${response.statusText}`;
}

export async function loadSkillHubDetail(
  location: SkillHubSkillLocation,
): Promise<SkillHubDetail> {
  const response = await globalThis.fetch(
    `${SKILLHUB_BASE_PATH}/api/web/skills/${skillHubApiIdentityPath(location.namespace, location.slug)}`,
    { credentials: "include", cache: "no-store" },
  );
  if (!response.ok) throw new Error(await readError(response));
  const detail = readSkillHubDetail(await response.json());
  if (!detail) throw new Error("SkillHub 返回的技能版本信息不完整。");
  return detail;
}

export async function loadSkillHubRuntimeSkills(): Promise<
  SkillHubRuntimeSkill[]
> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/skillhub/runtime/skills`,
    { cache: "no-store" },
  );
  if (!response.ok) throw new Error(await readError(response));
  const body = (await response.json()) as { skills?: SkillHubRuntimeSkill[] };
  return Array.isArray(body.skills) ? body.skills : [];
}

export async function installSkillHubRuntimeSkill(
  detail: SkillHubDetail,
): Promise<SkillHubRuntimeInstallResult> {
  const download = await globalThis.fetch(
    skillHubDownloadPath(detail.namespace, detail.slug, detail.version),
    { credentials: "include", cache: "no-store" },
  );
  if (!download.ok) throw new Error(await readError(download));

  const form = new FormData();
  form.set("namespace", detail.namespace);
  form.set("slug", detail.slug);
  form.set("version", detail.version);
  form.set(
    "file",
    await download.blob(),
    `${detail.slug}-${detail.version}.zip`,
  );

  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/skillhub/runtime/skills`,
    { method: "POST", body: form },
  );
  if (!response.ok) throw new Error(await readError(response));
  return response.json() as Promise<SkillHubRuntimeInstallResult>;
}

export async function setSkillHubRuntimeEnabled(
  skill: SkillHubSkillLocation,
  enabled: boolean,
): Promise<SkillHubRuntimeSkill> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/skillhub/runtime/skills/${skillHubApiIdentityPath(skill.namespace, skill.slug)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
  if (!response.ok) throw new Error(await readError(response));
  return response.json() as Promise<SkillHubRuntimeSkill>;
}

export async function uninstallSkillHubRuntimeSkill(
  skill: SkillHubSkillLocation,
): Promise<void> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/skillhub/runtime/skills/${skillHubApiIdentityPath(skill.namespace, skill.slug)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw new Error(await readError(response));
}
