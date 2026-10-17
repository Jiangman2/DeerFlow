// Dependency-free release gate. Does not run the application or make requests.
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const digest = (raw) => crypto.createHash("sha256").update(raw).digest("hex");

export function verify(root, assets = false, maxBytes = 1024 ** 3) {
  root = fs.realpathSync(root);
  const read = (name) => fs.readFileSync(path.join(root, name));
  assert(
    fs.statSync(path.join(root, "server.js")).isFile(),
    "standalone_server_missing",
  );
  const metadata = JSON.parse(read(".next/required-server-files.json"));
  assert.equal(metadata.config.output, "standalone", "not_standalone");
  assert(
    !metadata.config.typescript?.ignoreBuildErrors,
    "type_checks_disabled",
  );
  const routes = JSON.parse(read(".next/server/app-paths-manifest.json"));
  for (const route of [
    "/api/runtime-config/route",
    "/workspace/chats/[thread_id]/page",
    "/login/page",
  ]) {
    // Route groups may appear in App Router manifest keys.
    assert(
      Object.keys(routes).some(
        (key) => key.replace(/\/\([^/]+\)/g, "") === route,
      ),
      "required_route_missing:" + route,
    );
  }
  const id = read(".next/BUILD_ID").toString().trim();
  assert(id.length > 0, "build_id_missing");
  for (const name of [
    "src",
    "tests",
    ".git",
    ".env",
    ".env.local",
    ".next/cache",
    "pnpm-lock.yaml",
    "node_modules/typescript",
    "node_modules/@playwright",
  ]) {
    assert(
      !fs.existsSync(path.join(root, name)),
      "unexpected_runtime_content:" + name,
    );
  }
  if (assets) {
    for (const name of ["public", ".next/static"]) {
      assert(
        fs.statSync(path.join(root, name)).isDirectory(),
        "assets_missing:" + name,
      );
      assert(
        fs.readdirSync(path.join(root, name)).length > 0,
        "assets_empty:" + name,
      );
    }
  }
  let files = 0,
    bytes = 0,
    symlinks = 0;
  function walk(dir) {
    for (const name of fs.readdirSync(dir)) {
      const file = path.join(dir, name),
        info = fs.lstatSync(file);
      assert(
        !/^\.env(?:\.|$)/.test(name) && !/\.(pem|key|p12|pfx)$/.test(name),
        "credential_file_in_runtime",
      );
      if (info.isSymbolicLink()) {
        const relative = path.relative(root, fs.realpathSync(file));
        assert(
          !relative.startsWith("..") && !path.isAbsolute(relative),
          "escaping_runtime_symlink",
        );
        symlinks++;
      } else if (info.isDirectory()) walk(file);
      else {
        assert(info.isFile(), "special_runtime_file");
        bytes += info.size;
        files++;
      }
    }
  }
  walk(root);
  assert(bytes <= maxBytes, "runtime_size_budget_exceeded");
  return {
    standalone_layout: "PASS",
    files,
    bytes,
    symlinks,
    assets_checked: assets,
    build_id_sha256: digest(id),
    routes_sha256: digest(read(".next/routes-manifest.json")),
    runtime_http: "NOT_TESTED",
    production_acceptance: "NOT_TESTED",
  };
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    console.log(
      JSON.stringify(
        verify(process.argv[2], process.argv.includes("--assets")),
      ),
    );
  } catch (error) {
    console.error(
      JSON.stringify({ standalone_layout: "FAIL", check: error.message }),
    );
    process.exitCode = 1;
  }
}
