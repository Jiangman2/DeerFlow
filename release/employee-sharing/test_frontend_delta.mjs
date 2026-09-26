import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import vm from "node:vm";

import { compose, composeLocale, digest, loadDelta, localeProperty, paths } from "./frontend_delta.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const baseline = join(here, "audited-baseline-20260924-dependencies");
const frontend = resolve(here, "../../frontend");
const ts = createRequire(join(frontend, "package.json"))("typescript");
const available = existsSync(join(baseline, "SOURCE-BASELINE.json"));
const evidenceTest = (name, fn) => test(name, { skip: !available }, fn);
function fixture(relative) {
  const evidence = JSON.parse(readFileSync(join(baseline, "SOURCE-BASELINE.json"))).components.frontend;
  return {
    evidence,
    source: Object.hasOwn(evidence.files, relative) ? readFileSync(join(baseline, "frontend", relative)) : null,
    candidate: readFileSync(join(frontend, relative)),
  };
}

evidenceTest("all 33 reviewed deltas compose but cannot claim rollout readiness", () => {
  const result = loadDelta(baseline, frontend);
  assert.equal(result.files.size, 33);
  assert.equal(result.ready_for_rollout, false);
  assert.deepEqual(result.unresolved_source_evidence, []);
  for (const receipt of result.receipts) assert.equal(digest(result.files.get(receipt.path)), receipt.target_sha256);
  for (const protectedPath of ["package.json", "pnpm-lock.yaml", "tsconfig.json", "src/components/ui/dialog.tsx"]) assert.equal(result.files.has(protectedPath), false);
  assert.match(result.files.get("src/app/workspace/agents/new/page.tsx").toString(), /expected_draft_revision: fetched.draft_revision/);
});

evidenceTest("history retains employee origin without importing unrelated channel labels", () => {
  const relative = "src/core/threads/utils.ts";
  const { source, candidate, evidence } = fixture(relative);
  const result = compose(relative, source, candidate, evidence).toString();
  assert.match(result, /parseAgentSource\(source\)/);
  assert.match(result, /source=invalid/);
  assert.match(result, /feishu: "Feishu"/);
  assert.equal(result.slice(result.indexOf("export function textOfMessage")), source.toString().slice(source.toString().indexOf("export function textOfMessage")));
});

evidenceTest("composed history routes execute personal/team/invalid origins and preserve ordinary chat", () => {
  const execute = (text, require) => {
    const exports = {};
    const code = ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    vm.runInNewContext(code, { exports, require });
    return exports;
  };
  const route = fixture("src/core/agents/routing.ts");
  const routing = execute(compose("src/core/agents/routing.ts", route.source, route.candidate, route.evidence).toString(), () => { throw Error("Unexpected dependency"); });
  const utils = fixture("src/core/threads/utils.ts");
  const { pathOfThread, channelSourceOfThread } = execute(compose("src/core/threads/utils.ts", utils.source, utils.candidate, utils.evidence).toString(), (name) => {
    assert.equal(name, "../agents/routing");
    return routing;
  });
  assert.equal(pathOfThread("thread"), "/workspace/chats/thread");
  assert.equal(pathOfThread("thread", { agent_name: "员工", agent_source: "personal" }), "/workspace/agents/%E5%91%98%E5%B7%A5/chats/thread?source=personal");
  assert.equal(pathOfThread({ thread_id: "thread", metadata: { agent_name: "same", agent_source: "team" } }), "/workspace/agents/same/chats/thread?source=team");
  assert.equal(pathOfThread({ thread_id: "thread", metadata: { agent_name: "same", agent_source: { bad: true } } }), "/workspace/agents/same/chats/thread?source=invalid");
  assert.equal(channelSourceOfThread({ metadata: { channel_source: { type: "im_channel", provider: "feishu" } } }).label, "Feishu");
});

evidenceTest("employee KB selector keeps ordinary chat tags enabled by default", () => {
  const relative = "src/components/workspace/knowledge-scope-selector.tsx";
  const { source, candidate, evidence } = fixture(relative);
  const result = compose(relative, source, candidate, evidence).toString();
  assert.match(result, /allowTags = true/);
  assert.match(result, /enabled: open && allowTags/);
  assert.equal(result, candidate.toString());
});

evidenceTest("old incomplete source evidence cannot masquerade as a complete delta", () => {
  const old = join(here, "audited-baseline-20260924-supplement");
  assert.throws(() => loadDelta(old, frontend), /Missing explicit absence evidence|Existing .* baseline required/);
});

evidenceTest("Slash helper imports neither unrelated reserved-name policy nor new dependencies", () => {
  const relative = "src/components/workspace/input-box-helpers.ts";
  const { source, candidate, evidence } = fixture(relative);
  const result = compose(relative, source, candidate, evidence).toString();
  assert.doesNotMatch(result, /RESERVED_SLASH_SKILL_NAMES|reservedNames/);
  assert.equal(result.replace('Pick<Skill, "name" | "description" | "enabled">[]', "Skill[]"), source.toString());
});

for (const locale of ["en-US", "zh-CN", "types"]) {
  evidenceTest(`${locale}: only agents and the two invocation messages change`, () => {
    const relative = `src/core/i18n/locales/${locale}.ts`;
    const { source, candidate, evidence } = fixture(relative);
    const result = compose(relative, source, candidate, evidence).toString();
    assert.equal(localeProperty(result, ["agents"]), localeProperty(candidate.toString(), ["agents"]));
    assert.equal(localeProperty(result, ["inputBox", "employeeSkillsUnavailable"]), localeProperty(candidate.toString(), ["inputBox", "employeeSkillsUnavailable"]));
    assert.notEqual(result, candidate.toString(), "Unrelated scheduler changes must stay out");
    assert.equal(localeProperty(result, ["inputBox", "placeholder"]), localeProperty(source.toString(), ["inputBox", "placeholder"]));
  });
}

evidenceTest("candidate change after review is rejected", () => {
  const relative = "src/core/agents/api.ts";
  const { source, candidate, evidence } = fixture(relative);
  assert.throws(() => compose(relative, source, Buffer.concat([candidate, Buffer.from("\n// changed")]), evidence), /Candidate changed after review/);
});

evidenceTest("a different frontend image must be reviewed even with overlapping source", () => {
  const relative = "src/core/agents/api.ts";
  const { source, candidate, evidence } = fixture(relative);
  evidence.image_id = "sha256:" + "0".repeat(64);
  assert.throws(() => compose(relative, source, candidate, evidence), /production image differs/);
});

evidenceTest("production source tampering is rejected before patching", () => {
  const relative = "src/core/agents/api.ts";
  const { source, candidate, evidence } = fixture(relative);
  assert.throws(() => compose(relative, Buffer.concat([source, Buffer.from("\n")]), candidate, evidence), /Production source digest mismatch/);
});

evidenceTest("new file requires explicit production absence, not just unavailable source", () => {
  const relative = "src/core/skills/invocation.ts";
  const { source, candidate, evidence } = fixture(relative);
  assert.equal(source, null);
  assert.equal(compose(relative, source, candidate, evidence).toString(), candidate.toString());
  evidence.missing = evidence.missing.filter((p) => p !== relative);
  assert.throws(() => compose(relative, source, candidate, evidence), /Missing explicit absence evidence/);
});

evidenceTest("contradictory presence and absence evidence is rejected", () => {
  const relative = "src/core/agents/api.ts";
  const { source, candidate, evidence } = fixture(relative);
  evidence.missing.push(relative);
  assert.throws(() => compose(relative, source, candidate, evidence), /Contradictory absence evidence/);
});

test("unknown paths including dependency manifests are never composed", () => {
  assert.equal(paths.includes("package.json"), false);
  for (const path of ["package.json", "../../package.json", "src/app/workspace/scheduled-tasks/page.tsx"]) assert.throws(() => compose(path, null, Buffer.from(""), {}), /Unreviewed employee frontend path/);
});

test("locale syntax and repeated roots fail closed", () => {
  assert.throws(() => composeLocale("broken {", "broken {"), /Invalid frontend source/);
  assert.throws(() => localeProperty("export const enUS = {}; export const zhCN = {};", ["agents"]), /Ambiguous translation root/);
  assert.throws(() => localeProperty("export const enUS = {agents: {}, agents: {}};", ["agents"]), /Translation property missing or ambiguous/);
});
