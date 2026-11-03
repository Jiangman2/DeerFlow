import assert from "node:assert/strict";
import { copyFile, mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("patches a v0.8 Vite entry without retaining v0.7 asset names", async () => {
  const root = await mkdtemp(join(tmpdir(), "weknora-v08-ui-patch-"));
  const dist = join(root, "dist");
  const overlay = join(root, "enterprise");
  await mkdir(dist);
  await mkdir(overlay);
  await writeFile(
    join(dist, "index.html"),
    '<!doctype html><html><head><script type="module" crossorigin src="/assets/index-v08Hash.js"></script></head><body><div id="app"></div></body></html>',
  );
  for (const name of [
    "deerflow-enterprise-bootstrap.js",
    "deerflow-enterprise.css",
  ]) {
    await copyFile(join(packageRoot, "overlay", name), join(overlay, name));
  }

  const result = spawnSync(
    process.execPath,
    [join(packageRoot, "overlay", "patch-enterprise-ui.mjs")],
    {
      cwd: root,
      env: { ...process.env, ENTERPRISE_OVERLAY_DIR: overlay },
      encoding: "utf8",
    },
  );
  assert.equal(result.status, 0, result.stderr || result.stdout);
  const html = await readFile(join(dist, "index.html"), "utf8");
  assert.match(html, /__WEKNORA_MAIN_MODULE__ = "\/assets\/index-v08Hash\.js"/);
  assert.match(html, /deerflow-enterprise-bootstrap\.js\?v=weknora-v08-sso2-domguard1/);
  assert.match(html, /deerflow-enterprise\.css\?v=weknora-v08-sso2-domguard1/);
  assert.doesNotMatch(html, /<script type="module" crossorigin src="\/assets\/index-v08Hash\.js"><\/script>/);
  assert.doesNotMatch(html, /main-BUSZvhEQ|index-itUW58et/);

  const buildInfo = JSON.parse(
    await readFile(join(dist, "deerflow-build-info.json"), "utf8"),
  );
  assert.equal(buildInfo.ui_version, "0.8.0");
  assert.equal(buildInfo.enterprise_layer, "sso2-domguard1");
});
