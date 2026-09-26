/** Read-only employee frontend composer. Not an image builder or rollout tool.
 * Exact, reviewed candidate hashes prevent later workspace edits from slipping
 * into this delta. Shared locale and Slash files are selected below the file
 * boundary. Package manifests, channel/PDF code and scheduler changes stay out.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, lstatSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(resolve(here, "../../frontend/package.json"));
const ts = require("typescript");
const review = JSON.parse(readFileSync(join(here, "frontend-review.json"), "utf8"));
export const paths = Object.freeze(Object.keys(review.reviewed_candidates));

export function digest(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function parse(text, name = "source.ts") {
  const tree = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true);
  assert.equal(tree.parseDiagnostics.length, 0, `Invalid frontend source: ${name}`);
  return tree;
}

function root(tree) {
  const found = [];
  for (const statement of tree.statements) {
    if (ts.isInterfaceDeclaration(statement) && statement.name.text === "Translations") found.push(statement);
    if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        if (["enUS", "zhCN"].includes(declaration.name.getText(tree)) && declaration.initializer && ts.isObjectLiteralExpression(declaration.initializer)) found.push(declaration.initializer);
      }
    }
  }
  assert.equal(found.length, 1, "Ambiguous translation root");
  return found[0];
}

function child(node, name) {
  const found = [...(node.members ?? node.properties)].filter((p) => p.name?.getText().replace(/^['"]|['"]$/g, "") === name);
  assert.equal(found.length, 1, `Translation property missing or ambiguous: ${name}`);
  return found[0];
}

function inner(property) {
  const node = property.type ?? property.initializer;
  assert.ok(node && (ts.isTypeLiteralNode(node) || ts.isObjectLiteralExpression(node)), "Unexpected translation property shape");
  return node;
}

export function localeProperty(text, names) {
  const tree = parse(text);
  let node = root(tree);
  for (let i = 0; i < names.length; i += 1) {
    const property = child(node, names[i]);
    if (i === names.length - 1) return text.slice(property.getStart(tree), property.end);
    node = inner(property);
  }
  throw Error("Empty property path");
}

export function composeLocale(source, candidate) {
  const tree = parse(source);
  const parent = root(tree);
  const agents = child(parent, "agents");
  const input = inner(child(parent, "inputBox"));
  const names = ["employeeSkillsLoading", "employeeSkillsUnavailable"];
  assert.ok([...(input.members ?? input.properties)].every((p) => !names.includes(p.name?.getText())), "Employee locale additions already exist");
  const ending = ts.isObjectLiteralExpression(input) ? "," : "";
  const additions = names.map((name) => "    " + localeProperty(candidate, ["inputBox", name]) + ending).join("\n");
  const patches = [
    [agents.getStart(tree), agents.end, localeProperty(candidate, ["agents"])],
    [input.getStart(tree) + 1, input.getStart(tree) + 1, "\n" + additions],
  ];
  let result = source;
  for (const [start, end, replacement] of patches.sort((a, b) => b[0] - a[0])) result = result.slice(0, start) + replacement + result.slice(end);
  parse(result);
  // Byte-for-byte preservation below the shared-file boundary.
  for (const property of parent.members ?? parent.properties) {
    const name = property.name.getText();
    if (!["agents", "inputBox"].includes(name)) assert.equal(localeProperty(result, [name]), localeProperty(source, [name]));
  }
  for (const property of input.members ?? input.properties) {
    const name = property.name.getText();
    assert.equal(localeProperty(result, ["inputBox", name]), localeProperty(source, ["inputBox", name]));
  }
  return result;
}

function composeThreadRoutes(source, candidate) {
  const tree = parse(source);
  const proposed = parse(candidate);
  const selected = ["ThreadRouteTarget", "pathOfThread"];
  const find = (ast, name) => {
    const matches = ast.statements.filter((s) => s.name?.getText(ast) === name);
    assert.equal(matches.length, 1, `Ambiguous thread route symbol: ${name}`);
    return matches[0];
  };
  const patches = selected.map((name) => {
    const original = find(tree, name);
    const replacement = find(proposed, name);
    return [original.getStart(tree), original.end, candidate.slice(replacement.getStart(proposed), replacement.end)];
  });
  const routeImports = proposed.statements.filter((s) => ts.isImportDeclaration(s) && s.moduleSpecifier.text === "../agents/routing");
  assert.equal(routeImports.length, 1, "Reviewed routing import missing");
  assert.ok(!tree.statements.some((s) => ts.isImportDeclaration(s) && s.moduleSpecifier.text === "../agents/routing"), "Routing import already present");
  let result = source;
  for (const [start, end, replacement] of patches.sort((a, b) => b[0] - a[0])) result = result.slice(0, start) + replacement + result.slice(end);
  const routeImport = routeImports[0];
  result = candidate.slice(routeImport.getStart(proposed), routeImport.end) + "\n" + result;
  const composed = parse(result);
  // Besides the two selected declarations, all original statements—including
  // channel presentation and metadata helpers—must retain their exact bytes.
  const retained = composed.statements.filter((s) => !selected.includes(s.name?.getText(composed)) && !(ts.isImportDeclaration(s) && s.moduleSpecifier.text === "../agents/routing"));
  const original = tree.statements.filter((s) => !selected.includes(s.name?.getText(tree)));
  assert.deepEqual(retained.map((s) => result.slice(s.getStart(composed), s.end)), original.map((s) => source.slice(s.getStart(tree), s.end)));
  return result;
}

export function compose(relative, source, candidate, evidence) {
  assert.ok(paths.includes(relative), `Unreviewed employee frontend path: ${relative}`);
  assert.equal(evidence.image_id?.replace(/^containerd:\/\//, ""), review.source_image_id, "Frontend production image differs from review");
  assert.equal(digest(candidate), review.reviewed_candidates[relative], `Candidate changed after review: ${relative}`);
  if (source === null) {
    assert.ok(evidence.missing.includes(relative) && !Object.hasOwn(evidence.files, relative), `Missing explicit absence evidence: ${relative}`);
  } else {
    assert.equal(digest(source), evidence.files[relative], `Production source digest mismatch: ${relative}`);
    assert.ok(!evidence.missing.includes(relative), `Contradictory absence evidence: ${relative}`);
  }
  let result = candidate.toString("utf8");
  if (relative.startsWith("src/core/i18n/locales/")) {
    assert.ok(source, "Existing locale baseline required");
    result = composeLocale(source.toString("utf8"), result);
  } else if (relative === "src/core/threads/utils.ts") {
    assert.ok(source, "Existing thread routing baseline required");
    result = composeThreadRoutes(source.toString("utf8"), result);
  } else if (relative.endsWith("/input-box-helpers.ts")) {
    assert.ok(source, "Existing Slash helper baseline required");
    const anchor = "export function getMatchingSkillSuggestions(\n  skills: Skill[],";
    const addition = 'export function getMatchingSkillSuggestions(\n  skills: Pick<Skill, "name" | "description" | "enabled">[],';
    const original = source.toString("utf8");
    assert.equal(original.split(anchor).length, 2, "Ambiguous Slash helper anchor");
    assert.equal(result.split(addition).length, 2, "Reviewed helper change missing");
    result = original.replace(anchor, addition);
  }
  parse(result, relative);
  return Buffer.from(result);
}

function readPlain(rootPath, relative) {
  assert.ok(!relative.includes("\\") && !relative.includes(":") && relative.split("/").every((p) => p && p !== "." && p !== ".."), "Unsafe source path");
  let cursor = resolve(rootPath);
  for (const part of ["", ...relative.split("/")]) {
    cursor = join(cursor, part);
    const stat = lstatSync(cursor);
    assert.ok(!stat.isSymbolicLink(), `Linked frontend source: ${relative}`);
  }
  const stat = lstatSync(cursor);
  assert.ok(stat.isFile() && stat.size <= 2 * 1024 ** 2, "Unsafe source member");
  return readFileSync(cursor);
}

export function loadDelta(baseline, candidateRoot) {
  const lock = JSON.parse(readPlain(baseline, "SOURCE-BASELINE.json"));
  const evidence = lock.components.frontend;
  assert.match(evidence.image_id, /^(containerd:\/\/)?sha256:[a-f0-9]{64}$/);
  const files = new Map();
  const receipts = [];
  for (const path of paths) {
    const source = Object.hasOwn(evidence.files, path) ? readPlain(join(baseline, "frontend"), path) : null;
    const candidate = readPlain(candidateRoot, path);
    const target = compose(path, source, candidate, evidence);
    files.set(path, target);
    receipts.push({ path, source_sha256: source ? digest(source) : null, candidate_sha256: digest(candidate), target_sha256: digest(target) });
  }
  return { files, receipts, image: evidence.image, image_id: evidence.image_id, ready_for_rollout: false, unresolved_source_evidence: [] };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const [baseline, candidateRoot] = process.argv.slice(2);
  assert.ok(baseline && candidateRoot, "Usage: node frontend_delta.mjs BASELINE FRONTEND");
  const { files, ...report } = loadDelta(baseline, candidateRoot);
  console.log(JSON.stringify({ ...report, composed_files: files.size, production_mutation: "NONE", scope: "reviewed_partial_delta_not_dependency_closure" }, null, 2));
}
