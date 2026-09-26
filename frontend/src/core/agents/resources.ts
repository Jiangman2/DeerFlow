import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import { AgentPublicationError } from "./api";

export interface EmployeeThreadPublication {
  bound: boolean;
  name?: string;
  revision?: string;
  current_revision?: string;
  update_available?: boolean;
  knowledge_mode?: "inherit" | "selected" | "disabled";
}

export type EmployeeResource =
  | { kind: "skill"; skill_name: string; content_digest: string }
  | {
      kind: "knowledge";
      tenant_id: string;
      kb_id: string;
      document_id?: string | null;
    };

export interface EmployeeGrant {
  id: string;
  revision: string;
  publication_id: string;
  publisher: string;
  approver: string | null;
  resource: EmployeeResource;
  state: "pending" | "approved" | "rejected" | "revoked";
  expires_at: string | null;
}

export interface PublicationCheck {
  publication_id: string;
  draft_revision: string;
  resources: { resource: EmployeeResource; grants: EmployeeGrant[] }[];
}

export async function resourceRequest<T>(
  path: string,
  body?: unknown,
): Promise<T> {
  const result = await fetch(`${getBackendBaseURL()}/api/${path}`, {
    ...(body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  if (!result.ok) {
    const error = (await result.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    throw new AgentPublicationError(
      result.status,
      typeof error.detail === "string"
        ? error.detail
        : "Employee resource operation failed",
    );
  }
  return result.json() as Promise<T>;
}

export function checkPublication(name: string, revision: string) {
  return resourceRequest<PublicationCheck>(
    `agents/${encodeURIComponent(name)}/publication-check`,
    { expected_draft_revision: revision },
  );
}

export function requestEmployeeGrant(
  name: string,
  revision: string,
  resource: EmployeeResource,
) {
  return resourceRequest<EmployeeGrant>(
    `agents/${encodeURIComponent(name)}/resource-grants`,
    { expected_draft_revision: revision, resource },
  );
}

export function activeGrant(grants: EmployeeGrant[]) {
  return grants.find(
    (grant) =>
      grant.state === "approved" &&
      grant.expires_at &&
      Date.parse(grant.expires_at) > Date.now(),
  );
}

export function resourceLabel(resource: EmployeeResource) {
  return resource.kind === "skill"
    ? `Skill · ${resource.skill_name}`
    : `Knowledge · ${resource.kb_id}${resource.document_id ? ` / ${resource.document_id}` : ""}`;
}
