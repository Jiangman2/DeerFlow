import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve, sep } from "node:path";

const [rootArgument, mode] = process.argv.slice(2);
if (!rootArgument || (mode && mode !== "--check-only")) {
  throw new Error("Usage: node apply_frontend.mjs ROOT [--check-only]");
}
const root = resolve(rootArgument);
const target = resolve(root, "src/components/workspace/messages/markdown-link.tsx");
if (!target.startsWith(root + sep)) throw new Error("Unsafe frontend target");
const digest = (value) => createHash("sha256").update(value).digest("hex");
const raw = readFileSync(target);
const actual = digest(raw);
const expected = "a11c882b53936ee34489a5ffeda5db3daec1df3b6c9be95381d775e0aa2c3164";
if (actual !== expected) throw new Error(`Audited frontend source mismatch: ${actual}`);
if (raw.includes(13)) throw new Error("Unexpected CRLF frontend source");

let source = raw.toString("utf8");
function replaceOnce(oldText, newText, label) {
  const first = source.indexOf(oldText);
  if (first < 0 || source.indexOf(oldText, first + oldText.length) >= 0) {
    throw new Error(`Expected exactly one occurrence: ${label}`);
  }
  source = source.slice(0, first) + newText + source.slice(first + oldText.length);
}

replaceOnce(
  '  if (!href || !threadId) {\n    return null;\n  }\n  let parsed: URL;\n  try {\n    parsed = new URL(href);\n',
  '  if (!href || !threadId) {\n    return null;\n  }\n' +
    '  // Autolinkers can absorb Chinese prose after an exact loopback origin.\n' +
    '  const previewOrigin =\n' +
    '    /^https?:\\/\\/(?:127\\.0\\.0\\.1|localhost|\\[::1\\]):5050(?=$|[/?#。！？；，])/i.exec(\n' +
    '      href,\n' +
    '    )?.[0];\n' +
    '  const suffix = previewOrigin ? href.slice(previewOrigin.length) : "";\n' +
    '  const candidate =\n' +
    '    previewOrigin && /^[。！？；，]/.test(suffix) ? `${previewOrigin}/` : href;\n' +
    '  let parsed: URL;\n  try {\n    parsed = new URL(candidate);\n',
  "loopback resolver",
);
replaceOnce(
  '  return `${prefix}${parsed.pathname || "/"}${parsed.search}`;',
  '  return `${prefix}${parsed.pathname || "/"}${parsed.search}${parsed.hash}`;',
  "URL fragment",
);
replaceOnce(
  '    const livePreviewURL = resolveLivePreviewURL(href, threadId);\n    if (livePreviewURL) {\n      const { className, target, rel, children, ...rest } = props;\n      const visibleChildren =',
  '    const livePreviewURL = resolveLivePreviewURL(href, threadId);\n    if (livePreviewURL) {\n' +
    '      const { className, target, rel, children, ...rest } = props;\n' +
    '      const trailingProse =\n' +
    '        typeof children === "string"\n' +
    '          ? /^https?:\\/\\/(?:127\\.0\\.0\\.1|localhost|\\[::1\\]):5050([。！？；，].+)$/i.exec(\n' +
    '              children,\n' +
    '            )?.[1]\n' +
    '          : undefined;\n' +
    '      const visibleChildren =',
  "preserve prose",
);
replaceOnce(
  '      return (\n        <a\n          {...rest}\n          href={livePreviewURL}',
  '      const previewLink = (\n        <a\n          {...rest}\n          href={livePreviewURL}',
  "preview anchor",
);
replaceOnce(
  '          {visibleChildren}\n        </a>\n      );\n    }',
  '          {visibleChildren}\n        </a>\n      );\n' +
    '      return trailingProse ? (\n' +
    '        <>\n          {previewLink}\n          {trailingProse}\n        </>\n' +
    '      ) : (\n        previewLink\n      );\n    }',
  "prose outside anchor",
);

const output = Buffer.from(source, "utf8");
if (mode !== "--check-only") writeFileSync(target, output);
process.stdout.write(
  `preview_delta=PASS component=frontend source=${actual} target=${digest(output)} mode=${mode === "--check-only" ? "check" : "apply"}\n`,
);
