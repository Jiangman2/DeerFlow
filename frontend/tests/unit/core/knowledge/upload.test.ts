import { describe, expect, test } from "@rstest/core";

import { knowledgeFilesFromInput } from "@/core/knowledge/upload";

function file(name: string): File {
  return new File(["content"], name, { type: "text/plain" });
}

function fileList(files: File[]): FileList {
  const list = {
    item: (index: number) => files[index] ?? null,
    [Symbol.iterator]: files[Symbol.iterator].bind(files),
  } as Record<number, File> & Pick<FileList, "item" | typeof Symbol.iterator>;
  Object.defineProperty(list, "length", { value: files.length });
  files.forEach((item, index) => {
    list[index] = item;
  });
  return list as unknown as FileList;
}

describe("knowledge upload helpers", () => {
  test("copies FileList into a stable array before the input is cleared", () => {
    const first = file("alpha.txt");
    const second = file("beta.txt");

    const copied = knowledgeFilesFromInput(fileList([first, second]));

    expect(copied).toEqual([first, second]);
  });

  test("handles empty input", () => {
    expect(knowledgeFilesFromInput(null)).toEqual([]);
  });
});
