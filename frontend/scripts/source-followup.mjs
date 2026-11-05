// Qualify the later dashboard edit before checking its historical formatter output.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

export const manifestPath =
  "release/enterprise-source-followup-20261103/SOURCE.json";
const manifestSha256 =
  "d383005667ae3f623045ae54fe333ba98677ec3ea03096914b23daf59342d1bc";
const dashboardPath = "frontend/src/app/workspace/admin/dashboard/page.tsx";
export const navigationManifestPath =
  "release/dashboard-navigation-20261103-r2/SOURCE.json";
const navigationManifestSha256 =
  "5a8f98a6ebd63175e83f6db294e2c7f826efaa6662b69c66d7043147b7086b92";
const digest = (raw) =>
  createHash("sha256")
    .update(raw.toString("utf8").replaceAll("\r\n", "\n"))
    .digest("hex");

async function enterpriseDashboard(path, current, read) {
  let raw;
  try {
    raw = await read(navigationManifestPath);
  } catch (error) {
    if (error.code === "ENOENT") return current;
    throw error;
  }
  assert.equal(
    digest(raw),
    navigationManifestSha256,
    "Navigation source seal changed",
  );
  const manifest = JSON.parse(raw.toString("utf8"));
  assert.equal(manifest.schema, "dashboard-navigation-source.v1");
  assert.equal(manifest.status, "CANDIDATE_SOURCE_NOT_DEPLOYMENT_ACCEPTANCE");
  assert.equal(manifest.production_acceptance_claimed, false);
  const row = manifest.changes[path];
  assert.ok(row, "Navigation dashboard registration missing");
  assert.equal(
    digest(current),
    row.current_sha256,
    "Current dashboard changed",
  );
  assert.equal(
    row.baseline_path,
    `release/dashboard-navigation-20261103-r2/baseline/${path}`,
  );
  const baseline = await read(row.baseline_path);
  assert.equal(
    digest(baseline),
    row.base_sha256,
    "Navigation parent dashboard changed",
  );
  return baseline;
}

export async function formattingSource(path, read) {
  let current = await read(path);
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
  current = await enterpriseDashboard(path, current, read);
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
