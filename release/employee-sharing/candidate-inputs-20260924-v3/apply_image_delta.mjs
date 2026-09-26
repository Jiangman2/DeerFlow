/** Frontend image-stage installer. No dependency install or network activity.
 * Validate every source/dependency/payload before writes. Not a live deployment
 * tool or multi-file transaction: discard a failed disposable build layer.
 */
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const LIMIT = 2 * 1024 ** 2;
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
function safePath(name) {
  assert.ok(typeof name === "string" && name && !/[\\:\0]/.test(name), "Unsafe delta path");
  assert.ok(name.split("/").every((p) => p && p !== "." && p !== ".." && !/[ .]$/.test(p)), "Unsafe delta path");
  return name;
}
function statOrMissing(target) {
  try { return fs.lstatSync(target); }
  catch (error) { if (error.code === "ENOENT") return null; throw error; }
}
function checkPath(target) {
  for (let parent = path.dirname(target); ; parent = path.dirname(parent)) {
    const info = statOrMissing(parent);
    assert.ok(!info || (info.isDirectory() && !info.isSymbolicLink()), "Unsafe source ancestor");
    if (parent === path.dirname(parent)) break;
  }
  const info = statOrMissing(target);
  assert.ok(!info || (info.isFile() && !info.isSymbolicLink() && info.size <= LIMIT), "Unsafe source file");
  return info;
}
export function prepare(rootArg, document) {
  const root = path.resolve(rootArg);
  assert.ok(fs.statSync(root).isDirectory(), "Image source root missing");
  assert.ok(document.schema === 1 && document.scope === "employee-sharing-image-candidate" && document.ready_for_rollout === false, "Not an employee image candidate");
  assert.ok(document.component === "frontend" && /^sha256:[a-f0-9]{64}$/.test(document.source_image_id), "Unsupported image identity");
  assert.ok(Array.isArray(document.entries) && document.entries.length >= 1 && document.entries.length <= 128, "Invalid delta scope");
  assert.ok(document.anchors && typeof document.anchors === "object" && !Array.isArray(document.anchors) && Object.keys(document.anchors).length <= 512, "Invalid anchors");
  const seen = new Set();
  const prepared = document.entries.map((entry) => {
    const name = safePath(entry.path);
    assert.ok(name.startsWith("src/") && /\.tsx?$/.test(name) && !seen.has(name.toLowerCase()), "Duplicate or unreviewable delta path");
    seen.add(name.toLowerCase());
    assert.ok(entry.source_sha256 === null || /^[a-f0-9]{64}$/.test(entry.source_sha256), "Invalid source checksum");
    assert.match(entry.target_sha256, /^[a-f0-9]{64}$/);
    const content = Buffer.from(entry.content_base64, "base64");
    assert.equal(content.toString("base64"), entry.content_base64, "Noncanonical payload encoding");
    assert.ok(content.length <= LIMIT && sha(content) === entry.target_sha256, "Delta content checksum mismatch");
    const target = path.join(root, name), info = checkPath(target);
    const before = info ? sha(fs.readFileSync(target)) : null;
    assert.ok(before === entry.source_sha256 || before === entry.target_sha256, `Image source differs: ${name}`);
    assert.ok(!info || !(info.mode & 0o7000), "Special source mode is forbidden");
    return { target, content, before, info };
  });
  for (const [name, checksum] of Object.entries(document.anchors)) {
    safePath(name);
    assert.ok(!seen.has(name.toLowerCase()) && /^[a-f0-9]{64}$/.test(checksum), "Conflicting image anchor");
    seen.add(name.toLowerCase());
    const target = path.join(root, name);
    assert.ok(checkPath(target) && sha(fs.readFileSync(target)) === checksum, `Image dependency anchor differs: ${name}`);
  }
  return prepared;
}
export function apply(root, document, checkOnly = true) {
  const prepared = prepare(root, document);
  if (!checkOnly) {
    for (const entry of prepared) {
      const current = checkPath(entry.target);
      assert.equal(current ? sha(fs.readFileSync(entry.target)) : null, entry.before, "Image source changed during apply");
      if (entry.before === sha(entry.content)) continue;
      fs.mkdirSync(path.dirname(entry.target), { recursive: true });
      const temporary = entry.target + ".employee-tmp-" + randomUUID();
      try {
        const fd = fs.openSync(temporary, "wx", 0o600);
        try { fs.writeFileSync(fd, entry.content); fs.fsyncSync(fd); }
        finally { fs.closeSync(fd); }
        fs.chmodSync(temporary, entry.info ? entry.info.mode & 0o777 : 0o644);
        if (entry.info && process.platform !== "win32") fs.chownSync(temporary, entry.info.uid, entry.info.gid);
        fs.renameSync(temporary, entry.target);
      } finally {
        if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
      }
    }
  }
  return { scope: "image-stage-only", mode: checkOnly ? "check-only" : "apply", files: prepared.length, ready_for_rollout: false };
}
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    const [root, manifest, mode, extra] = process.argv.slice(2);
    assert.ok(root && manifest && !extra && ["--check-only", "--image-build-stage"].includes(mode), "Explicit image-build-stage or check-only mode required");
    assert.ok(fs.statSync(manifest).size <= 64 * 1024 ** 2, "Image manifest too large");
    const raw = fs.readFileSync(manifest, "utf8");
    const document = JSON.parse(raw);
    // The generator emits this exact form. Round-trip equality rejects duplicate
    // object keys (JSON.parse alone silently accepts them) and ambiguous encodings.
    assert.equal(raw, JSON.stringify(document, null, 2) + "\n", "Noncanonical or duplicate-key manifest");
    console.log(JSON.stringify(apply(root, document, mode === "--check-only")));
  } catch (error) {
    console.error("employee_image_delta=FAIL detail=" + error.message);
    process.exitCode = 1;
  }
}
