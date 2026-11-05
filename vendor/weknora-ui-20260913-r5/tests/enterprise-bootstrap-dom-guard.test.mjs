import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const bootstrapPath = resolve(
  packageRoot,
  "overlay",
  "deerflow-enterprise-bootstrap.js",
);

async function loadBrandFunctions() {
  const source = await readFile(bootstrapPath, "utf8");
  const start = source.indexOf("function replaceBrand");
  const end = source.indexOf("function updateDocumentTitle");
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);

  class FakeElement {}
  const context = {
    Element: FakeElement,
    Node: { TEXT_NODE: 3 },
  };
  const functions = vm.runInNewContext(
    `${source.slice(start, end)}\n({ replaceBrand, brandNode })`,
    context,
  );
  return { ...functions, FakeElement, source };
}

test("brand replacement does not rewrite an already normalized text node", async () => {
  const { brandNode } = await loadBrandFunctions();
  let value = "知识库";
  let writes = 0;
  const node = {
    nodeType: 3,
    parentElement: { closest: () => null },
    get nodeValue() {
      return value;
    },
    set nodeValue(nextValue) {
      writes += 1;
      value = nextValue;
    },
  };

  brandNode(node);
  assert.equal(writes, 0);
  assert.equal(value, "知识库");

  value = "WeKnora";
  brandNode(node);
  assert.equal(writes, 1);
  assert.equal(value, "知识库");

  brandNode(node);
  assert.equal(writes, 1);
});

test("brand replacement does not rewrite unchanged attributes", async () => {
  const { brandNode, FakeElement } = await loadBrandFunctions();
  class AttributeElement extends FakeElement {
    constructor(value) {
      super();
      this.value = value;
      this.writes = 0;
      this.childNodes = [];
    }
    closest() {
      return null;
    }
    hasAttribute(name) {
      return name === "title";
    }
    getAttribute() {
      return this.value;
    }
    setAttribute(_name, value) {
      this.writes += 1;
      this.value = value;
    }
  }

  const element = new AttributeElement("知识库");
  brandNode(element);
  assert.equal(element.writes, 0);

  element.value = "WeKnora Console";
  brandNode(element);
  assert.equal(element.writes, 1);
  assert.equal(element.value, "知识库 Console");
});

test("startup waits for the real Vue mount and exposes async failures", async () => {
  const { source } = await loadBrandFunctions();
  assert.match(source, /nextValue !== currentValue/);
  assert.match(source, /waitForApplicationMount/);
  assert.match(source, /unhandledrejection/);
  assert.match(source, /root\?\.hasAttribute\("data-v-app"\)/);
});
