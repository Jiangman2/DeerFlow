import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { verify } from "./verify-standalone.mjs";

function fixture(t) {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "deerflow-standalone-test-"),
  );
  t.after(() => fs.rmSync(root, { recursive: true })); // Only this test's unique temp directory.
  const write = (name, data) => {
    const file = path.join(root, name);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(
      file,
      typeof data === "string" ? data : JSON.stringify(data),
    );
  };
  write("server.js", "// synthetic fixture");
  write(".next/required-server-files.json", {
    config: { output: "standalone" },
  });
  write(".next/BUILD_ID", "synthetic");
  write(".next/routes-manifest.json", {});
  write(".next/server/app-paths-manifest.json", {
    "/api/runtime-config/route": "route.js",
    "/(main)/workspace/chats/[thread_id]/page": "page.js",
    "/(auth)/login/page": "login.js",
  });
  write("public/icon.svg", "<svg/>");
  write(".next/static/test.js", "// static");
  return { root, write };
}

test("accepts minimal standalone with route groups and assets", (t) => {
  const { root } = fixture(t);
  assert.equal(verify(root, true).standalone_layout, "PASS");
});
for (const name of [
  "src/page.tsx",
  ".env.local",
  "node_modules/typescript/index.js",
  "private.key",
]) {
  test("rejects unnecessary or secret runtime file: " + name, (t) => {
    const { root, write } = fixture(t);
    write(name, "test");
    assert.throws(() => verify(root, true));
  });
}
test("requires routes instead of treating build success as acceptance", (t) => {
  const { root, write } = fixture(t);
  write(".next/server/app-paths-manifest.json", {});
  assert.throws(() => verify(root), /required_route_missing/);
});
test("retains TypeScript build gate", (t) => {
  const { root, write } = fixture(t);
  write(".next/required-server-files.json", {
    config: { output: "standalone", typescript: { ignoreBuildErrors: true } },
  });
  assert.throws(() => verify(root), /type_checks_disabled/);
});
test("requires separately copied static assets", (t) => {
  const { root } = fixture(t);
  fs.unlinkSync(path.join(root, ".next/static/test.js"));
  assert.throws(() => verify(root, true), /assets_empty/);
});
test("bounds output bytes", (t) => {
  const { root } = fixture(t);
  assert.throws(() => verify(root, true, 1), /runtime_size_budget/);
});
