/** Operator-approved destinations. No model- or user-supplied navigation. */
export const USER_GUIDE_URL =
  "https://hnwork.w.cnooc.com.cn/docx/doxkjqbGzuarsaBp3oeDtVmcBug";

// Retain legacy asset metadata for older release consumers. Portal navigation
// now renders pre-sized embedded 3D bitmaps, without fetching these legacy files.
export const PORTAL_TOOLS = [
  {
    id: "ppt",
    placement: "ppt",
    href: "https://ppt.cnooc.com.cn/aippt/home",
    artwork: "/images/action-ppt.png",
    artworkScale: 1,
  },
  {
    id: "proofread",
    placement: "quick",
    href: "https://proofread.cnooc.com.cn/#/check",
    artwork: "/images/portal-proofread.png",
    artworkScale: 709 / 951,
  },
  {
    id: "translate",
    placement: "quick",
    href: "https://translator.cnooc.com.cn/#/documentTranslation",
    artwork: "/images/portal-translate.png",
    artworkScale: 709 / 969,
  },
  {
    id: "liveTranscribe",
    placement: "more",
    href: "https://meeting.cnooc.com.cn/gateway/static/#/home/conference/index",
    artwork: "/images/portal-live-transcribe.png",
    artworkScale: 709 / 1114,
  },
  {
    id: "convert",
    placement: "more",
    href: "https://translator.cnooc.com.cn/#/wordToPdf?module=format",
    artwork: "/images/portal-convert.png",
    artworkScale: 709 / 929,
  },
  {
    id: "recordTranscribe",
    placement: "more",
    href: "https://meeting.cnooc.com.cn/gateway/static/indexzx.html#/home/officeAssistant/record",
    artwork: "/images/portal-record-transcribe.png",
    artworkScale: 709 / 1041,
  },
  {
    id: "speech",
    placement: "more",
    href: "https://vm.cnooc.com.cn/",
    artwork: "/images/portal-speech.png",
    artworkScale: 709 / 1087,
  },
] as const;

export type PortalTool = (typeof PORTAL_TOOLS)[number];
export const PPT_ASSISTANT_TOOL = PORTAL_TOOLS[0];
export const PORTAL_QUICK_TOOLS = PORTAL_TOOLS.filter(
  (tool) => tool.placement === "quick",
);
export const PORTAL_MORE_TOOLS = PORTAL_TOOLS.filter(
  (tool) => tool.placement === "more",
);
