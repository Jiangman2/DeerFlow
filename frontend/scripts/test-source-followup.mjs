import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

import {
  formattingSource,
  manifestPath,
  navigationManifestPath,
} from "./source-followup.mjs";

const root = resolve(import.meta.dirname, "../..");
const dashboard = "frontend/src/app/workspace/admin/dashboard/page.tsx";
const baseline = `release/enterprise-source-followup-20261103/baseline/${dashboard}`;
const navigationBaseline = `release/dashboard-navigation-20261103-r2/baseline/${dashboard}`;
const read = (path) => readFile(resolve(root, path));
const changed = (target) => async (path) => {
  const raw = await read(path);
  return path === target
    ? Buffer.concat([raw, Buffer.from("\n/* drift */\n")])
    : raw;
};

test("checks the live dashboard and returns its exact historical bytes", async () => {
  assert.equal(
    await formattingSource(dashboard, read),
    (await read(baseline)).toString("utf8"),
  );
});
test("rejects an unregistered live dashboard edit", async () => {
  await assert.rejects(
    formattingSource(dashboard, changed(dashboard)),
    /Current dashboard changed/,
  );
});
test("rejects a changed historical formatter input", async () => {
  await assert.rejects(
    formattingSource(dashboard, changed(baseline)),
    /Historical dashboard changed/,
  );
});
test("rejects an edited successor seal", async () => {
  await assert.rejects(
    formattingSource(dashboard, changed(manifestPath)),
    /Enterprise source seal changed/,
  );
});

test("rejects an edited navigation seal", async () => {
  await assert.rejects(
    formattingSource(dashboard, changed(navigationManifestPath)),
    /Navigation source seal changed/,
  );
});

test("rejects a changed navigation parent snapshot", async () => {
  await assert.rejects(
    formattingSource(dashboard, changed(navigationBaseline)),
    /Navigation parent dashboard changed/,
  );
});

test("supports the enterprise parent before navigation registration", async () => {
  const parentRead = async (path) => {
    if (path === navigationManifestPath)
      throw Object.assign(new Error("missing"), { code: "ENOENT" });
    return read(path === dashboard ? navigationBaseline : path);
  };
  assert.equal(
    await formattingSource(dashboard, parentRead),
    (await read(baseline)).toString("utf8"),
  );
});
test("leaves the unchanged CSS under the original formatter check", async () => {
  const css = "frontend/src/styles/cnooc-concept.css";
  assert.equal(
    await formattingSource(css, read),
    (await read(css)).toString("utf8"),
  );
});
test("supports a historical checkout without a successor manifest", async () => {
  const historical = async (path) => {
    if (path === manifestPath)
      throw Object.assign(new Error("missing"), { code: "ENOENT" });
    return read(path);
  };
  assert.equal(
    await formattingSource(dashboard, historical),
    (await read(dashboard)).toString("utf8"),
  );
});
