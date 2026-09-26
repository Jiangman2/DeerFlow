/** Export reviewed bytes to the local candidate composer; no files are changed. */
import assert from "node:assert/strict";
import { loadDelta } from "./frontend_delta.mjs";

const [baseline, candidate, extra] = process.argv.slice(2);
assert.ok(baseline && candidate && !extra, "Expected baseline and frontend root");
const delta = loadDelta(baseline, candidate);
process.stdout.write(JSON.stringify({
  image_id: delta.image_id,
  entries: delta.receipts.map((receipt) => ({
    ...receipt,
    content_base64: delta.files.get(receipt.path).toString("base64"),
  })),
}));
