import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { waitFor } from "@testing-library/dom";

import {
  initializeWeKnoraMobileNavigation,
  observeWeKnoraModal,
} from "@/core/weknora/mobile-embed";

const cleanups: Array<() => void> = [];
afterEach(() => {
  cleanups.splice(0).forEach((fn) => fn());
  rs.restoreAllMocks();
});
const settle = () => new Promise((resolve) => setTimeout(resolve, 30));

function setup(mobile = true, path = "/platform/knowledge-bases") {
  rs.spyOn(window, "matchMedia").mockReturnValue({
    matches: mobile,
  } as MediaQueryList);
  const doc = document.implementation.createHTMLDocument();
  const frame = {
    contentDocument: doc,
    contentWindow: {
      location: { origin: window.location.origin, pathname: path },
    },
  } as unknown as HTMLIFrameElement;
  return { doc, frame };
}
function mount(doc: Document) {
  doc.body.innerHTML =
    '<main class="main"><aside class="aside_box"><button class="sidebar-toggle">Menu</button><button class="menu_item">Knowledge</button></aside></main>';
  const sidebar = doc.querySelector("aside")!;
  const toggle = doc.querySelector("button")!;
  toggle.addEventListener("click", () => {
    queueMicrotask(() => sidebar.classList.toggle("aside_box--collapsed"));
  });
  return { sidebar, toggle };
}
describe("native knowledge mobile navigation", () => {
  it.each(["settings-overlay", "upload-confirm-overlay"])(
    "hides host navigation for visible %s and restores it on close",
    async (overlayClass) => {
      const { doc, frame } = setup();
      const changed = rs.fn();
      const cleanup = observeWeKnoraModal(frame, changed);
      cleanups.push(cleanup);
      const overlay = doc.createElement("div");
      overlay.className = overlayClass;
      const rects = rs
        .spyOn(overlay, "getClientRects")
        .mockReturnValue([] as unknown as DOMRectList);
      doc.body.appendChild(overlay);
      await settle();
      expect(changed.mock.calls).toEqual([[false]]);
      rects.mockReturnValue([{}] as unknown as DOMRectList);
      overlay.classList.add("visible");
      await waitFor(() =>
        expect(changed.mock.calls).toEqual([[false], [true]]),
      );
      overlay.appendChild(doc.createElement("span"));
      await settle();
      expect(changed).toHaveBeenCalledTimes(2);
      overlay.style.visibility = "hidden";
      await waitFor(() =>
        expect(changed.mock.calls).toEqual([[false], [true], [false]]),
      );
      overlay.remove();
      await settle();
      expect(changed.mock.calls).toEqual([[false], [true], [false]]);
      cleanup();
      doc.body.appendChild(overlay);
      await settle();
      expect(changed).toHaveBeenCalledTimes(3);
    },
  );
  it("waits for SSO mount, collapses once and preserves manual reopening", async () => {
    const { doc, frame } = setup();
    const cleanup = initializeWeKnoraMobileNavigation(frame);
    cleanups.push(cleanup);
    const { sidebar, toggle } = mount(doc);
    await settle();
    expect(sidebar.classList.contains("aside_box--collapsed")).toBe(true);
    toggle.click();
    doc.body.appendChild(doc.createElement("div"));
    await settle();
    expect(sidebar.classList.contains("aside_box--collapsed")).toBe(false);
    cleanup();
    cleanups.push(initializeWeKnoraMobileNavigation(frame));
    await settle();
    expect(sidebar.classList.contains("aside_box--collapsed")).toBe(false);
    doc.querySelector<HTMLButtonElement>(".menu_item")!.click();
    await settle();
    expect(sidebar.classList.contains("aside_box--collapsed")).toBe(true);
  });
  it("leaves desktop navigation expanded", async () => {
    const { doc, frame } = setup(false);
    const { sidebar } = mount(doc);
    cleanups.push(initializeWeKnoraMobileNavigation(frame));
    await settle();
    expect(sidebar.classList.contains("aside_box--collapsed")).toBe(false);
  });
  it("does not affect another embedded app", async () => {
    const { doc, frame } = setup(true, "/platform/skillhub");
    const { sidebar } = mount(doc);
    cleanups.push(initializeWeKnoraMobileNavigation(frame));
    await settle();
    expect(doc.querySelector("style")).toBeNull();
    expect(sidebar.classList.contains("aside_box--collapsed")).toBe(false);
  });
  it("disconnects before a late mount on navigation away", async () => {
    const { doc, frame } = setup();
    initializeWeKnoraMobileNavigation(frame)();
    const { sidebar } = mount(doc);
    await settle();
    expect(sidebar.classList.contains("aside_box--collapsed")).toBe(false);
  });
});
