import type { Skill } from "./type";

export async function loadSkills() {
  const skills = await fetch("/api/skills");
  if (!skills.ok) {
    const text = await skills.text().catch(() => "");
    throw new Error(text.trim() ? text : `HTTP ${skills.status}: ${skills.statusText}`);
  }
  const json = await skills.json();
  return json.skills as Skill[];
}

export async function enableSkill(skillName: string, enabled: boolean) {
  const encodedSkillName = encodeURIComponent(skillName);
  const response = await fetch(
    `/api/skills/${encodedSkillName}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        enabled,
      }),
    },
  );
  const text = await response.text().catch(() => "");
  if (!response.ok) {
    try {
      const data = JSON.parse(text) as { detail?: string };
      throw new Error(
        data.detail?.trim()
          ? data.detail
          : `HTTP ${response.status}: ${response.statusText}`,
      );
    } catch {
      throw new Error(text.trim() ? text : `HTTP ${response.status}: ${response.statusText}`);
    }
  }
  return text ? (JSON.parse(text) as unknown) : {};
}

export interface InstallSkillRequest {
  thread_id: string;
  path: string;
}

export interface InstallSkillResponse {
  success: boolean;
  skill_name: string;
  message: string;
}

export async function installSkill(
  request: InstallSkillRequest,
): Promise<InstallSkillResponse> {
  const response = await fetch("/api/skills/install", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    // Handle HTTP error responses (4xx, 5xx)
    const errorData = await response.json().catch(() => ({}));
    const errorMessage =
      errorData.detail ?? `HTTP ${response.status}: ${response.statusText}`;
    return {
      success: false,
      skill_name: "",
      message: errorMessage,
    };
  }

  return response.json();
}
