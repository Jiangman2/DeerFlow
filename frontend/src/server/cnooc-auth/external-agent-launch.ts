export const CNOOC_EXTERNAL_AGENT_TARGETS = {
  zywdjxzj: "https://datalake.cnooc/zywdjxzj",
  qqyjbgbxzj: "https://datalake.cnooc/qqyjbgbxzj",
  zjbgjszj: "https://datalake.cnooc/zjbgjszj/",
  zdsczj: "https://datalake.cnooc/zdsczj",
  dzsjtpzj: "https://datalake.cnooc/dzsjtpzj",
} as const;

export type CnoocExternalAgentKey =
  keyof typeof CNOOC_EXTERNAL_AGENT_TARGETS;

export function isCnoocExternalAgentKey(
  value: string,
): value is CnoocExternalAgentKey {
  return Object.prototype.hasOwnProperty.call(
    CNOOC_EXTERNAL_AGENT_TARGETS,
    value,
  );
}

export function buildCnoocExternalAgentTargetUrl(
  agent: CnoocExternalAgentKey,
  platformToken: string,
) {
  const token = platformToken.trim();
  if (!token) {
    throw new Error("platform token is missing");
  }
  if (token.length > 4096 || /[\u0000-\u001f\u007f]/.test(token)) {
    throw new Error("platform token is invalid");
  }

  const target = new URL(CNOOC_EXTERNAL_AGENT_TARGETS[agent]);
  target.searchParams.set("token", token);
  return target;
}
