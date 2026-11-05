import { copyFile, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const dist = resolve(process.cwd(), "dist");
const enterprise = process.env.ENTERPRISE_OVERLAY_DIR || "/enterprise/overlay";
const indexPath = resolve(dist, "index.html");
let html = await readFile(indexPath, "utf8");

if (!html.includes('<div id="app"></div>')) {
  throw new Error("upstream v0.8 UI entry point was not found");
}
if (html.includes("deerflow-enterprise-bootstrap.js")) {
  throw new Error("enterprise bootstrap is already present");
}

const moduleScripts = [...html.matchAll(/<script(?=[^>]*\btype=["']module["'])(?=[^>]*\bsrc=["']([^"']+)["'])[^>]*><\/script>/gi)];
if (moduleScripts.length !== 1) {
  throw new Error(`expected one Vite application module, found ${moduleScripts.length}`);
}
const [moduleTag, moduleUrl] = moduleScripts[0];
if (!/^\/assets\/[A-Za-z0-9_.-]+\.js$/.test(moduleUrl)) {
  throw new Error(`unexpected Vite application module URL: ${moduleUrl}`);
}

html = html.replace(moduleTag, "");
html = html.replace(
  "</head>",
  '  <link rel="stylesheet" href="/deerflow-enterprise.css?v=weknora-v08-sso2-domguard1" />\n</head>',
);
const bootstrap = [
  '  <script src="/config.js?v=weknora-v08-sso2-domguard1"></script>',
  `  <script>globalThis.__WEKNORA_MAIN_MODULE__ = ${JSON.stringify(moduleUrl)};</script>`,
  '  <script type="module" src="/deerflow-enterprise-bootstrap.js?v=weknora-v08-sso2-domguard1"></script>',
].join("\n");
html = html.replace('<div id="app"></div>', `${bootstrap}\n  <div id="app"></div>`);

for (const forbidden of ["main-BUSZvhEQ.js", "index-itUW58et.js"]) {
  if (html.includes(forbidden)) {
    throw new Error(`legacy v0.7 asset leaked into v0.8 index: ${forbidden}`);
  }
}
if (!html.includes(moduleUrl) || !html.includes("deerflow-enterprise-bootstrap.js")) {
  throw new Error("patched index lost its application or bootstrap entry");
}

await copyFile(resolve(enterprise, "deerflow-enterprise-bootstrap.js"), resolve(dist, "deerflow-enterprise-bootstrap.js"));
await copyFile(resolve(enterprise, "deerflow-enterprise.css"), resolve(dist, "deerflow-enterprise.css"));
await writeFile(
  resolve(dist, "deerflow-build-info.json"),
  `${JSON.stringify({
    ui_version: "0.8.0",
    ui_source_commit: "1edcd54b43606d9079bb36650efe3f68707a79ea",
    enterprise_layer: "sso2-domguard1",
  })}\n`,
  "utf8",
);
await writeFile(indexPath, html, "utf8");

console.log(`weknora_v08_main_module=${moduleUrl}`);
console.log("weknora_v08_enterprise_index_patch=PASS");
