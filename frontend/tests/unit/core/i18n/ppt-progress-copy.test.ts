import { expect, test } from "@rstest/core";

import {
  pptProgressCopy,
  pptSummaryValue,
} from "@/core/i18n/ppt-progress-copy";

test.each(["zh-CN", "en-US"])(
  "summary values retain their original fallback semantics in %s",
  (locale) => {
    const copy = pptProgressCopy(locale);
    expect(pptSummaryValue(copy, "page_count", "")).toBe(copy.unspecified);
    expect(pptSummaryValue(copy, "page_count", "0")).toBe("0");
    expect(pptSummaryValue(copy, "audience", "Engineers")).toBe("Engineers");
    expect(pptSummaryValue(copy, "template_mode", "unknown")).toBe(
      copy.unspecified,
    );
    expect(pptSummaryValue(copy, "template_mode", "templates")).toBe(
      copy.modes.templates,
    );
    expect(
      pptSummaryValue(
        { ...copy, modes: { empty: "" } },
        "template_mode",
        "empty",
      ),
    ).toBe(copy.unspecified);
  },
);
