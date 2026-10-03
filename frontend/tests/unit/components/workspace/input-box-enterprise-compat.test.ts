import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "@rstest/core";

const source = readFileSync(
  resolve(process.cwd(), "src/components/workspace/input-box.tsx"),
  "utf8",
);

describe("input box enterprise-browser compatibility", () => {
  test("the plain composer uses the selected locale placeholder", () => {
    const textarea = source.slice(
      source.indexOf("<PromptInputTextarea"),
      source.indexOf("<PromptInputFooter"),
    );

    expect(textarea).toContain("placeholder={t.inputBox.placeholder}");
    expect(textarea).not.toMatch(/placeholder="[^"]*"/);
  });

  test("voice recording uses a separate opacity layer", () => {
    const voiceButton = source.slice(
      source.indexOf("function VoiceInputButton"),
      source.indexOf("function SuggestionList"),
    );

    expect(voiceButton).toContain('className="bg-primary');
    expect(voiceButton).toContain("opacity-10");
    expect(voiceButton).not.toMatch(/bg-primary\/\d+/);
  });

  test("input polishing does not use slash-alpha primary surfaces", () => {
    const composer = source.slice(
      source.indexOf("<PromptInput"),
      source.indexOf("<PromptInputFooter"),
    );

    expect(composer).toContain('data-testid="polish-input-overlay"');
    expect(composer).toContain("bg-primary");
    expect(composer).toContain("opacity-[0.06]");
    expect(composer).not.toMatch(/(?:bg|border|ring|shadow)-primary\/\d+/);
  });
});
