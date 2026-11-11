// Qualify the later dashboard edit before checking its historical formatter output.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

export const manifestPath =
  "release/enterprise-source-followup-20261103/SOURCE.json";
const manifestSha256 =
  "d383005667ae3f623045ae54fe333ba98677ec3ea03096914b23daf59342d1bc";
const dashboardPath = "frontend/src/app/workspace/admin/dashboard/page.tsx";
const composerCss = "frontend/src/styles/cnooc-concept.css";
export const composerManifestPath =
  "release/history-composer-autosize-source-20261106/SOURCE.json";
const composerManifestSha256 =
  "f7d56bd203f490e0ca1abb3bc9890d76033205387e53a64bf7e793f8229b1bac";
const composerCssBaseline = `release/history-composer-autosize-source-20261106/baseline/${composerCss}`;
export const navigationManifestPath =
  "release/dashboard-navigation-20261103-r2/SOURCE.json";
const navigationManifestSha256 =
  "5a8f98a6ebd63175e83f6db294e2c7f826efaa6662b69c66d7043147b7086b92";
const digest = (raw) =>
  createHash("sha256")
    .update(raw.toString("utf8").replaceAll("\r\n", "\n"))
    .digest("hex");

async function composerFormattingSource(path, current, read) {
  if (path !== composerCss) return current;
  let raw;
  try {
    raw = await read(composerManifestPath);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    try {
      await read(composerCssBaseline);
    } catch (baselineError) {
      if (baselineError.code === "ENOENT") return current;
      throw baselineError;
    }
    assert.fail("Composer source manifest missing");
  }
  assert.equal(
    digest(raw),
    composerManifestSha256,
    "Composer source seal changed",
  );
  const manifest = JSON.parse(raw.toString("utf8"));
  assert.equal(manifest.schema, "history-composer-autosize-source.v1");
  assert.equal(manifest.status, "NOT_DEPLOYED");
  assert.equal(
    manifest.base_commit,
    "47a4fb82b2fae402c7fc95e6e9e3e93ad6187d50",
  );
  assert.equal(manifest.production_acceptance_claimed, false);
  assert.equal(manifest.runtime_modified_by_registration, false);
  const row = manifest.changes[path];
  assert.ok(row, "Composer CSS registration missing");
  assert.equal(row.kind, "runtime-source");
  assert.equal(
    digest(current),
    row.current_sha256,
    "Current composer CSS changed",
  );
  assert.equal(row.baseline_path, composerCssBaseline);
  const baseline = await read(row.baseline_path);
  assert.equal(
    digest(baseline),
    row.base_sha256,
    "Historical composer CSS changed",
  );
  return baseline;
}

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
  let current = await composerFormattingSource(path, await read(path), read);
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
