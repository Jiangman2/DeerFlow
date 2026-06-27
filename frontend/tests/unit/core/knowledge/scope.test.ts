import { describe, expect, test } from "@rstest/core";

import {
  buildKnowledgeScopeOptions,
  expandKnowledgeScopePaths,
  isKnowledgeScopeOptionChecked,
  updateKnowledgeScopeSelection,
} from "@/core/knowledge/scope";
import type { KnowledgeFile } from "@/core/knowledge/types";

function file(path: string, filename = path.split("/").at(-1) ?? path): KnowledgeFile {
  const folderPath = path.split("/").slice(0, -1).join("/");
  return {
    id: path,
    filename,
    folder_path: folderPath,
    display_path: path,
    original_filename: filename,
    content_type: "text/plain",
    size: 10,
    sha256: path,
    status: "indexed",
    error: null,
    chunk_count: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

describe("knowledge scope helpers", () => {
  test("builds nested folder and file options from uploaded files", () => {
    const options = buildKnowledgeScopeOptions([
      file("Projects/Alpha/brief.md"),
      file("Projects/Alpha/spec.md"),
      file("Projects/Beta/notes.md"),
    ]);

    expect(options.map((option) => `${option.type}:${option.path}`)).toEqual([
      "folder:Projects",
      "folder:Projects/Alpha",
      "file:Projects/Alpha/brief.md",
      "file:Projects/Alpha/spec.md",
      "folder:Projects/Beta",
      "file:Projects/Beta/notes.md",
    ]);

    expect(options.find((option) => option.path === "Projects/Alpha")?.filePaths).toEqual([
      "Projects/Alpha/brief.md",
      "Projects/Alpha/spec.md",
    ]);
  });

  test("selecting a folder expands to its files and stores the compact folder path", () => {
    const options = buildKnowledgeScopeOptions([
      file("Projects/Alpha/brief.md"),
      file("Projects/Alpha/spec.md"),
      file("Projects/Beta/notes.md"),
    ]);
    const alpha = options.find((option) => option.path === "Projects/Alpha");

    expect(alpha).toBeDefined();
    const selected = updateKnowledgeScopeSelection([], alpha!, true, options);
    expect(selected).toEqual(["Projects/Alpha"]);

    const expanded = expandKnowledgeScopePaths(selected, options);
    expect(expanded).toEqual(
      new Set(["Projects/Alpha/brief.md", "Projects/Alpha/spec.md"]),
    );
    expect(isKnowledgeScopeOptionChecked(alpha!, expanded)).toBe(true);
  });

  test("unchecking a file inside a selected folder keeps the remaining file selected", () => {
    const options = buildKnowledgeScopeOptions([
      file("Projects/Alpha/brief.md"),
      file("Projects/Alpha/spec.md"),
    ]);
    const spec = options.find((option) => option.path === "Projects/Alpha/spec.md");

    expect(spec).toBeDefined();
    const selected = updateKnowledgeScopeSelection(["Projects/Alpha"], spec!, false, options);

    expect(selected).toEqual(["Projects/Alpha/brief.md"]);
    expect(expandKnowledgeScopePaths(selected, options)).toEqual(
      new Set(["Projects/Alpha/brief.md"]),
    );
  });
});
