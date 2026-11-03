import { expect, test } from "@rstest/core";

import { resolveCitationHref } from "@/components/workspace/citations/citation-link";

test("redirects legacy WeKnora sandbox citations to the matching knowledge base", () => {
  expect(
    resolveCitationHref(
      '/mnt/user-data/knowledge_bases/kb-1/附件1："书记项目" 跟踪表.docx',
    ),
  ).toBe("/platform/knowledge-bases/kb-1");
});

test("keeps canonical WeKnora document citations unchanged", () => {
  const href = "/platform/knowledge-bases/kb-1?knowledge_id=doc-1";

  expect(resolveCitationHref(href)).toBe(href);
});
