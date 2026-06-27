import {
  afterEach,
  beforeEach,
  describe,
  expect,
  test,
  rs,
} from "@rstest/core";

const ENV_KEYS = [
  "NEXT_PUBLIC_BACKEND_BASE_URL",
  "NEXT_PUBLIC_STATIC_WEBSITE_ONLY",
] as const;

type EnvSnapshot = Partial<
  Record<(typeof ENV_KEYS)[number], string | undefined>
>;

function snapshotEnv(): EnvSnapshot {
  const snapshot: EnvSnapshot = {};
  for (const key of ENV_KEYS) {
    snapshot[key] = process.env[key];
  }
  return snapshot;
}

function setEnv(key: (typeof ENV_KEYS)[number], value: string | undefined) {
  const env = process.env as Record<string, string | undefined>;
  if (value === undefined) {
    delete env[key];
  } else {
    env[key] = value;
  }
}

function restoreEnv(snapshot: EnvSnapshot) {
  for (const key of ENV_KEYS) {
    setEnv(key, snapshot[key]);
  }
}

async function loadFreshArtifactUtils() {
  rs.resetModules();
  return await import("@/core/artifacts/utils");
}

describe("artifact URL helpers", () => {
  let saved: EnvSnapshot;

  beforeEach(() => {
    saved = snapshotEnv();
    setEnv("NEXT_PUBLIC_BACKEND_BASE_URL", undefined);
    setEnv("NEXT_PUBLIC_STATIC_WEBSITE_ONLY", undefined);
  });

  afterEach(() => {
    restoreEnv(saved);
  });

  test("maps static demo artifact paths to bundled public files", async () => {
    setEnv("NEXT_PUBLIC_STATIC_WEBSITE_ONLY", "true");

    const { resolveArtifactURL, urlOfArtifact } =
      await loadFreshArtifactUtils();

    expect(
      urlOfArtifact({
        filepath: "/mnt/user-data/outputs/index.html",
        threadId: "thread-1",
      }),
    ).toBe("/demo/threads/thread-1/user-data/outputs/index.html");
    expect(
      resolveArtifactURL("/mnt/user-data/outputs/style.css", "thread-1"),
    ).toBe("/demo/threads/thread-1/user-data/outputs/style.css");
  });

  test("builds artifact URLs with download and format query params", async () => {
    setEnv("NEXT_PUBLIC_BACKEND_BASE_URL", "http://127.0.0.1:8001");

    const { urlOfArtifact } = await loadFreshArtifactUtils();

    expect(
      urlOfArtifact({
        filepath: "/mnt/user-data/outputs/report.md",
        threadId: "thread-1",
        download: true,
        format: "pdf",
      }),
    ).toBe(
      "http://127.0.0.1:8001/api/threads/thread-1/artifacts/mnt/user-data/outputs/report.md?download=true&format=pdf",
    );
  });

  test("finds sibling PDF previews for PowerPoint artifacts", async () => {
    const { findPreviewPdfPath, guessPreviewPdfPath } =
      await loadFreshArtifactUtils();

    expect(guessPreviewPdfPath("/mnt/user-data/outputs/deck.pptx")).toBe(
      "/mnt/user-data/outputs/deck.preview.pdf",
    );
    expect(
      findPreviewPdfPath("/mnt/user-data/outputs/deck.pptx", [
        "/mnt/user-data/outputs/deck.pptx",
        "/mnt/user-data/outputs/deck.preview.pdf",
      ]),
    ).toBe("/mnt/user-data/outputs/deck.preview.pdf");
    expect(
      findPreviewPdfPath("/mnt/user-data/outputs/deck.pptx", [
        "/mnt/user-data/outputs/deck.pptx",
      ]),
    ).toBeNull();
  });
});
