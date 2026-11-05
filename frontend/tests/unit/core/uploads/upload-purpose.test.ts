import { expect, test } from "@rstest/core";

import { inferUploadPurpose } from "@/core/uploads/upload-purpose";

test("marks a PPTX attached to an explicit ppt-master task as a template", () => {
  expect(
    inferUploadPurpose("/ppt-master 请基于模板制作 5 页 PPT", [
      { name: "中海油模板.PPTX" },
    ]),
  ).toBe("presentation_template");
});

test("keeps ordinary presentation uploads on the document parsing route", () => {
  expect(
    inferUploadPurpose("请总结这份汇报材料", [{ name: "汇报材料.pptx" }]),
  ).toBe("attachment");
});

test("does not treat non-presentation ppt-master attachments as templates", () => {
  expect(
    inferUploadPurpose("/ppt-master 请制作演示文稿", [
      { name: "requirements.docx" },
    ]),
  ).toBe("attachment");
});
