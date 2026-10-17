import fs from "fs";
import path from "path";

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { NextRequest } from "next/server";

import { GET } from "@/app/mock/api/threads/[thread_id]/artifacts/[[...artifact_path]]/route";

afterEach(() => {
  rs.restoreAllMocks();
});

function request(parts: string[], thread = "demo-123", download = false) {
  return GET(
    new NextRequest(
      `http://localhost/mock/file${download ? "?download=true" : ""}`,
    ),
    {
      params: Promise.resolve({ thread_id: thread, artifact_path: parts }),
    },
  );
}

describe("demo artifact bounded tracing root", () => {
  it("reads the existing demo mapping under public/demo/threads", async () => {
    rs.spyOn(fs, "existsSync").mockReturnValue(true);
    rs.spyOn(fs, "realpathSync").mockImplementation((file) => String(file));
    rs.spyOn(fs, "statSync").mockReturnValue({
      isFile: () => true,
    } as fs.Stats);
    const read = rs
      .spyOn(fs, "readFileSync")
      .mockReturnValue(Buffer.from("test deck"));
    const result = await request(
      ["mnt", "user-data", "outputs", "example.txt"],
      "demo-123",
      true,
    );
    expect(result.status).toBe(200);
    expect(await result.text()).toBe("test deck");
    expect(read).toHaveBeenCalledWith(
      path.resolve(
        process.cwd(),
        "public/demo/threads",
        "demo-123",
        "user-data/outputs/example.txt",
      ),
    );
    expect(result.headers.get("Content-Disposition")).toBe(
      "attachment; filename*=UTF-8''example.txt",
    );
  });

  it.each([
    ["mnt", "..", "package.json"],
    ["mnt", "a/../../package.json"],
    ["mnt", "a\\..\\package.json"],
    ["mnt", "a\0.txt"],
    ["mnt", ""],
    ["mnt"],
    ["etc", "passwd"],
  ])(
    "rejects unsafe path without file access: %j",
    async (...parts: string[]) => {
      const exists = rs.spyOn(fs, "existsSync");
      expect((await request(parts)).status).toBe(404);
      expect(exists).not.toHaveBeenCalled();
    },
  );

  it("rejects escaping thread id", async () => {
    const exists = rs.spyOn(fs, "existsSync");
    expect((await request(["mnt", "a.txt"], "../other")).status).toBe(404);
    expect(exists).not.toHaveBeenCalled();
  });

  it("rejects an escaping symlink before reading bytes", async () => {
    rs.spyOn(fs, "existsSync").mockReturnValue(true);
    rs.spyOn(fs, "realpathSync").mockImplementation((file) =>
      String(file).endsWith("a.txt")
        ? path.resolve(process.cwd(), "package.json")
        : String(file),
    );
    const read = rs.spyOn(fs, "readFileSync");
    expect((await request(["mnt", "a.txt"])).status).toBe(404);
    expect(read).not.toHaveBeenCalled();
  });
});
