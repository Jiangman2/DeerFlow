// Qualify the later dashboard edit before checking its historical formatter output.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

export const manifestPath =
  "release/enterprise-source-followup-20261103/SOURCE.json";
const manifestSha256 =
  "d383005667ae3f623045ae54fe333ba98677ec3ea03096914b23daf59342d1bc";
const dashboardPath = "frontend/src/app/workspace/admin/dashboard/page.tsx";
const digest = (raw) =>
  createHash("sha256")
    .update(raw.toString("utf8").replaceAll("\r\n", "\n"))
    .digest("hex");

export async function formattingSource(path, read) {
  const current = await read(path);
  let raw;
  try {
    raw = await read(manifestPath);
  } catch (error) {
    if (error.code === "ENOENT") return current.toString("utf8");
    throw error;
  }
  assert.equal(digest(raw), manifestSha256, "Enterprise source seal changed");
  const manifest = JSON.parse(raw.toString("utf8"));
  assert.equal(manifest.schema, "enterprise-source-followup.v1");
  assert.equal(manifest.status, "COMMITTED_SOURCE_NOT_DEPLOYMENT_ACCEPTANCE");
  const row = manifest.changes[path];
  if (!row) return current.toString("utf8");
  assert.equal(path, dashboardPath, "Unexpected formatter successor path");
  assert.equal(
    digest(current),
    row.current_sha256,
    "Current dashboard changed",
  );
  assert.equal(
    row.baseline_path,
    `release/enterprise-source-followup-20261103/baseline/${path}`,
  );
  const baseline = await read(row.baseline_path);
  assert.equal(
    digest(baseline),
    row.base_sha256,
    "Historical dashboard changed",
  );
  return baseline.toString("utf8");
}
