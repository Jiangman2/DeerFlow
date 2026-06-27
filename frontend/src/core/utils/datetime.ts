import { formatDistanceToNow } from "date-fns";
import { enUS as dateFnsEnUS, zhCN as dateFnsZhCN } from "date-fns/locale";

import { detectLocale, type Locale } from "@/core/i18n";
import { getLocaleFromCookie } from "@/core/i18n/cookies";

const UNKNOWN_TIME_LABEL: Record<Locale, string> = {
  "en-US": "Unknown time",
  "zh-CN": "未知时间",
};

function getDateFnsLocale(locale: Locale) {
  switch (locale) {
    case "zh-CN":
      return dateFnsZhCN;
    case "en-US":
    default:
      return dateFnsEnUS;
  }
}

function toValidDate(date: Date | string | number) {
  const parsedDate = date instanceof Date ? date : new Date(date);
  return Number.isFinite(parsedDate.getTime()) ? parsedDate : null;
}

export function formatTimeAgo(date: Date | string | number, locale?: Locale) {
  const effectiveLocale =
    locale ??
    (getLocaleFromCookie() as Locale | null) ??
    // Fallback when cookie is missing (or on first render)
    detectLocale();

  const parsedDate = toValidDate(date);
  if (!parsedDate) {
    return UNKNOWN_TIME_LABEL[effectiveLocale];
  }

  return formatDistanceToNow(parsedDate, {
    addSuffix: true,
    locale: getDateFnsLocale(effectiveLocale),
  });
}
