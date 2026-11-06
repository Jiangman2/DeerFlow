import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

import {
  composerManifestPath,
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
test("checks the composer CSS successor before returning its formatter parent", async () => {
  const css = "frontend/src/styles/cnooc-concept.css";
  assert.equal(
    await formattingSource(css, read),
    (
      await read(
        `release/history-composer-autosize-source-20261106/baseline/${css}`,
      )
    ).toString("utf8"),
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

const composerCss = "frontend/src/styles/cnooc-concept.css";
const composerBaseline = `release/history-composer-autosize-source-20261106/baseline/${composerCss}`;

test("rejects an unregistered composer CSS edit", async () => {
  await assert.rejects(
    formattingSource(composerCss, changed(composerCss)),
    /Current composer CSS changed/,
  );
});

test("rejects an edited composer seal", async () => {
  await assert.rejects(
    formattingSource(composerCss, changed(composerManifestPath)),
    /Composer source seal changed/,
  );
});

test("rejects a changed composer formatter parent", async () => {
  await assert.rejects(
    formattingSource(composerCss, changed(composerBaseline)),
    /Historical composer CSS changed/,
  );
});

test("rejects a missing composer seal when its parent copy remains", async () => {
  const missing = async (path) => {
    if (path === composerManifestPath)
      throw Object.assign(new Error("missing"), { code: "ENOENT" });
    return read(path);
  };
  await assert.rejects(
    formattingSource(composerCss, missing),
    /Composer source manifest missing/,
  );
});

test("supports the historical CSS before the composer candidate", async () => {
  const old = async (path) => {
    if (path.startsWith("release/history-composer-autosize-source-20261106/"))
      throw Object.assign(new Error("missing"), { code: "ENOENT" });
    return read(path === composerCss ? composerBaseline : path);
  };
  assert.equal(
    await formattingSource(composerCss, old),
    (await read(composerBaseline)).toString("utf8"),
  );
});
