// Preserve the published source inventory while proving these runtime changes
// are exactly the repository's pinned formatter output, with no hand edits.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import * as prettier from "prettier";

const root = resolve(import.meta.dirname, "../..");
const manifest = JSON.parse(
  await readFile(
    resolve(root, "release/pr26-ci-qualification-20261103/SOURCE.json"),
    "utf8",
  ),
);
let checked = 0;
for (const [path, row] of Object.entries(manifest.changes)) {
  if (row.kind !== "prettier_only") continue;
  const filepath = resolve(root, path);
  const before = await readFile(resolve(root, row.baseline_path), "utf8");
  const after = await readFile(filepath, "utf8");
  const options = await prettier.resolveConfig(filepath);
  assert.equal(
    after.replaceAll("\r\n", "\n"),
    (await prettier.format(before, { ...options, filepath })).replaceAll(
      "\r\n",
      "\n",
    ),
    `Runtime change exceeds formatting: ${path}`,
  );
  checked += 1;
}
assert.equal(checked, 2);
console.log("Both runtime files match pinned Prettier output.");
