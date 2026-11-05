// Imagegen owns the artwork; this step only crops, resizes complete bitmaps and encodes them.
const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const sharp = require(
  require.resolve("sharp", {
    paths: [path.dirname(require.resolve("next/package.json"))],
  }),
);
const root = path.resolve(__dirname, "../../..");
const sources = {
  ppt: "exec-940a3e73-4197-4874-90da-ffe4f62fc859.png",
  proofread: "exec-d35ecee1-53bf-43cf-b41c-bbc0906dc38d.png",
  translate: "exec-3d534b00-6bd2-4ebb-b08b-e1b26b99b3f6.png",
  liveTranscribe: "exec-c270966b-0216-4adc-a5bd-6a22e12a2c0f.png",
  convert: "exec-bac96c4a-ba06-4e8c-a6ce-a71ad5100ae7.png",
  recordTranscribe: "exec-3c185a4c-5791-4587-b200-c1a7aef5e59b.png",
  speech: "exec-ede441f6-f11b-4bdf-95d3-3355e0972e43.png",
  more: "more.png",
};
const originals = {
  image: "public/images/action-image.png",
  pptAction: "public/images/action-ppt.png",
  research: "public/images/action-research.png",
  analysis: "public/images/action-analysis.png",
};
const canvas = 144,
  visible = 140;
const targetArrow = { width: 44, height: 42 };
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");
async function pixels(input) {
  return sharp(input).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
}
async function bounds(input) {
  const { data, info } = await pixels(input);
  let left = info.width,
    top = info.height,
    right = -1,
    bottom = -1;
  for (let y = 0; y < info.height; y++)
    for (let x = 0; x < info.width; x++)
      if (data[(y * info.width + x) * 4 + 3] >= 16) {
        left = Math.min(left, x);
        top = Math.min(top, y);
        right = Math.max(right, x);
        bottom = Math.max(bottom, y);
      }
  if (right < left) throw new Error("Empty bitmap");
  return { left, top, width: right - left + 1, height: bottom - top + 1 };
}
async function arrowBounds(input, allowCentered = false) {
  const { data, info } = await pixels(input);
  const n = info.width * info.height,
    mask = new Uint8Array(n),
    queue = new Int32Array(n);
  for (let p = 0; p < n; p++) {
    const i = p * 4,
      r = data[i],
      g = data[i + 1],
      b = data[i + 2],
      a = data[i + 3];
    mask[p] =
      a >= 128 && r > 170 && g > 40 && r > g * 1.12 && r > b * 1.6 ? 1 : 0;
  }
  const components = [];
  for (let p = 0; p < n; p++)
    if (mask[p]) {
      let head = 0,
        tail = 0,
        left = info.width,
        top = info.height,
        right = -1,
        bottom = -1;
      queue[tail++] = p;
      mask[p] = 0;
      while (head < tail) {
        const q = queue[head++],
          x = q % info.width,
          y = Math.floor(q / info.width);
        left = Math.min(left, x);
        top = Math.min(top, y);
        right = Math.max(right, x);
        bottom = Math.max(bottom, y);
        for (const next of [
          x > 0 ? q - 1 : -1,
          x + 1 < info.width ? q + 1 : -1,
          y > 0 ? q - info.width : -1,
          y + 1 < info.height ? q + info.width : -1,
        ])
          if (next >= 0 && mask[next]) {
            mask[next] = 0;
            queue[tail++] = next;
          }
      }
      if (
        tail > 20 &&
        left > info.width * (allowCentered ? 0.35 : 0.5) &&
        top > info.height * (allowCentered ? 0.35 : 0.5)
      )
        components.push({
          left,
          top,
          width: right - left + 1,
          height: bottom - top + 1,
          pixels: tail,
        });
    }
  components.sort((a, b) => b.pixels - a.pixels);
  if (!components.length) throw new Error("Navigation arrow not found");
  return components[0];
}
(async () => {
  const manifest = {
    mode: "built-in image_gen",
    canvas,
    visibleExtent: visible,
    arrowTarget: targetArrow,
    arrowMeasurement:
      "Largest lower-right connected orange silhouette; excludes white keyline. Whole-bitmap size normalization only; no separately painted or DOM-overlay arrow.",
    assets: [],
  };
  const rows = [];
  for (const id of [...Object.keys(sources), ...Object.keys(originals)]) {
    const input = await fs.readFile(
      originals[id]
        ? path.join(root, originals[id])
        : path.join(__dirname, id + ".png"),
    );
    const originalBounds = await bounds(input),
      navigation = id !== "more" && !originals[id];
    const crop = await sharp(input).extract(originalBounds).png().toBuffer();
    let width,
      height,
      observedArrow = null,
      originalArrow = null;
    if (navigation) {
      originalArrow = await arrowBounds(input);
      width = Math.round(
        (originalBounds.width * targetArrow.width) / originalArrow.width,
      );
      height = Math.round(
        (originalBounds.height * targetArrow.height) / originalArrow.height,
      );
    } else {
      const f = visible / Math.max(originalBounds.width, originalBounds.height);
      width = Math.round(originalBounds.width * f);
      height = Math.round(originalBounds.height * f);
    }
    let output;
    for (let attempt = 0; attempt < 8; attempt++) {
      if (width > canvas - 2 || height > canvas - 2)
        throw new Error(
          id +
            ": arrow normalization would clip the complete artwork: " +
            JSON.stringify({ width, height, originalArrow, originalBounds }),
        );
      output = await sharp(crop)
        .resize(width, height, { fit: "fill" })
        .extend({
          left: Math.floor((canvas - width) / 2),
          right: Math.ceil((canvas - width) / 2),
          top: Math.floor((canvas - height) / 2),
          bottom: Math.ceil((canvas - height) / 2),
          background: { r: 0, g: 0, b: 0, alpha: 0 },
        })
        .webp({ lossless: true, effort: 6 })
        .toBuffer();
      if (!navigation) break;
      observedArrow = await arrowBounds(output, true);
      if (
        observedArrow.width === targetArrow.width &&
        observedArrow.height === targetArrow.height
      )
        break;
      width += Math.sign(targetArrow.width - observedArrow.width);
      height += Math.sign(targetArrow.height - observedArrow.height);
    }
    if (
      navigation &&
      (observedArrow.width !== 44 || observedArrow.height !== 42)
    )
      throw new Error(id + ": inconsistent arrow dimensions");
    const webFile = "public/images/portal-3d-20261019-r3-" + id + ".webp";
    await fs.writeFile(path.join(root, webFile), output);
    const png = await sharp(output).png().toBuffer();
    await fs.mkdir(path.join(__dirname, "normalized"), { recursive: true });
    await fs.writeFile(path.join(__dirname, "normalized", id + ".png"), png);
    manifest.assets.push({
      id,
      origin: originals[id]
        ? "preserved application artwork"
        : "built-in image_gen",
      generatedSource: sources[id] || originals[id],
      pngSha256: sha(input),
      originalBounds,
      originalArrow,
      observedArrow,
      normalizedBounds: await bounds(output),
      webFile,
      webBytes: output.length,
      webSha256: sha(output),
      normalizedPng: "normalized/" + id + ".png",
      normalizedPngSha256: sha(png),
    });
    rows.push(
      "  " +
        id +
        ': "data:image/webp;base64,' +
        output.toString("base64") +
        '",',
    );
  }
  await fs.writeFile(
    path.join(root, "src/core/portal-icon-artwork.ts"),
    "// Generated by assets/portal-icons/20261019-r3/build-artwork.cjs.\n// Pre-sized transparent bitmaps: no image HTTP request or separate arrow overlay.\nexport const PORTAL_ICON_ARTWORK = {\n" +
      rows.join("\n") +
      "\n} as const;\n",
  );
  await fs.writeFile(
    path.join(__dirname, "manifest.json"),
    JSON.stringify(manifest, null, 2) + "\n",
  );
  console.log(
    JSON.stringify(
      {
        canvas,
        arrowTarget: targetArrow,
        webBytes: manifest.assets.reduce((n, a) => n + a.webBytes, 0),
        assets: manifest.assets.map(
          ({ id, observedArrow, normalizedBounds }) => ({
            id,
            observedArrow,
            normalizedBounds,
          }),
        ),
      },
      null,
      2,
    ),
  );
})();
