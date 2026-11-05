export type UploadPurpose = "attachment" | "presentation_template";

const PPT_MASTER_SLASH_COMMAND = /^\/ppt-master(?:\s|$)/i;
const PRESENTATION_EXTENSIONS = [".ppt", ".pptx"] as const;

export function inferUploadPurpose(
  text: string,
  files: readonly Pick<File, "name">[],
): UploadPurpose {
  if (!PPT_MASTER_SLASH_COMMAND.test(text.trim())) {
    return "attachment";
  }

  const hasPresentation = files.some((file) => {
    const filename = file.name.toLowerCase();
    return PRESENTATION_EXTENSIONS.some((extension) =>
      filename.endsWith(extension),
    );
  });
  return hasPresentation ? "presentation_template" : "attachment";
}
