import fs from "fs";
import path from "path";

import type { NextRequest } from "next/server";

export async function GET(
  request: NextRequest,
  {
    params,
  }: {
    params: Promise<{
      thread_id: string;
      artifact_path?: string[] | undefined;
    }>;
  },
) {
  const { thread_id: threadId, artifact_path: parts = [] } = await params;
  if (
    /^[a-zA-Z0-9_-]+$/.test(threadId) &&
    parts[0] === "mnt" &&
    parts.length > 1 &&
    parts.every(
      (part) =>
        part !== "" && part !== "." && part !== ".." && !/[\\/\0]/.test(part),
    )
  ) {
    // A literal subdirectory lets Next trace only demo assets, not the entire
    // project. Never resolve a client-controlled path against process.cwd().
    const threadRoot = path.resolve(
      process.cwd(),
      "public/demo/threads",
      threadId,
    );
    const artifactPath = path.resolve(
      process.cwd(),
      "public/demo/threads",
      threadId,
      ...parts.slice(1),
    );
    const relative = path.relative(threadRoot, artifactPath);
    if (
      !relative.startsWith("..") &&
      !path.isAbsolute(relative) &&
      fs.existsSync(artifactPath)
    ) {
      const realRelative = path.relative(
        fs.realpathSync(threadRoot),
        fs.realpathSync(artifactPath),
      );
      if (
        realRelative.startsWith("..") ||
        path.isAbsolute(realRelative) ||
        !fs.statSync(artifactPath).isFile()
      ) {
        return new Response("File not found", { status: 404 });
      }
      if (request.nextUrl.searchParams.get("download") === "true") {
        // Attach the file to the response
        const headers = new Headers();
        headers.set(
          "Content-Disposition",
          `attachment; filename*=UTF-8''${encodeURIComponent(path.basename(artifactPath))}`,
        );
        return new Response(fs.readFileSync(artifactPath), {
          status: 200,
          headers,
        });
      }
      if (artifactPath.endsWith(".mp4")) {
        return new Response(fs.readFileSync(artifactPath), {
          status: 200,
          headers: {
            "Content-Type": "video/mp4",
          },
        });
      }
      return new Response(fs.readFileSync(artifactPath), { status: 200 });
    }
  }
  return new Response("File not found", { status: 404 });
}
