import { describe, expect, it } from "@rstest/core";

import {
  DEFAULT_LOCALE,
  getLocaleByLang,
  normalizeLocale,
} from "@/core/i18n/locale";

describe("enterprise locale defaults", () => {
  it("uses Simplified Chinese for first render and missing locale state", () => {
    expect(DEFAULT_LOCALE).toBe("zh-CN");
    expect(normalizeLocale(undefined)).toBe("zh-CN");
  });

  it("keeps explicit English while unknown languages fall back to Chinese", () => {
    expect(normalizeLocale("en-US")).toBe("en-US");
    expect(getLocaleByLang("en")).toBe("en-US");
    expect(normalizeLocale("fr-FR")).toBe("zh-CN");
  });
});
