import crypto from "node:crypto";

export type CnoocTokenCheckResult = {
  ok: true;
  provider: "cnooc";
  platformTokenHash: string;
  user: {
    platformUserId: string;
    deerflowUserId: string;
    name: string;
    email: string;
  };
  roles: Array<{ code: string; name: string }>;
  department: { code: string; name: string };
};

function digestToken(token: string) {
  return crypto.createHash("sha1").update(token, "utf8").digest("hex");
}

export async function mockCheckCnoocToken(
  token: string,
): Promise<CnoocTokenCheckResult> {
  const normalized = token.trim();
  if (!normalized) throw new Error("token is required");

  const digest = digestToken(normalized);
  const short = digest.slice(0, 12);
  const platformUserId = `mock-${short}`;
  const deerflowUserId = `cnooc_${short}`;

  return {
    ok: true,
    provider: "cnooc",
    platformTokenHash: digest,
    user: {
      platformUserId,
      deerflowUserId,
      name: `CNOOC Mock User ${short.slice(0, 6)}`,
      email: `${deerflowUserId}@mock.cnooc.local`,
    },
    roles: [{ code: "mock-user", name: "Mock User" }],
    department: { code: "mock-dept", name: "Mock Department" },
  };
}
