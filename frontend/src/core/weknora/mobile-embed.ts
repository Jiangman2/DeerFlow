/** Presentation-only rules for the same-origin native WeKnora workspace.
 * Keep its routes, sidebar state, permissions and release assets authoritative.
 */
const MOBILE_STYLE_ID = "deerflow-weknora-mobile-layout";
const initializedMobileDocuments = new WeakSet<Document>();

const MOBILE_CSS = `
/* Host palette only. Native success/warning/error tokens retain their meaning. */
:root {
  --td-brand-color: #0067b1 !important;
  --td-brand-color-hover: #005592 !important;
  --td-brand-color-active: #004878 !important;
  --td-brand-color-light: #eaf3fb !important;
  --td-brand-color-light-hover: #dcecf8 !important;
  --td-text-color-primary: #20364d !important;
  --td-text-color-secondary: #526579 !important;
  --td-text-color-placeholder: #697b8e !important;
  --td-bg-color-page: #f5f7fa !important;
  --td-bg-color-container: #ffffff !important;
  --td-bg-color-sidebar: #f8fafc !important;
  --td-bg-color-secondarycontainer: #eaf3fb !important;
  --td-bg-color-container-hover: #f0f3f7 !important;
  --td-component-stroke: #dce4ed !important;
  --td-component-border: #bccbd9 !important;
}
.kb-list-container, .org-list-container, .platform-route-outlet {
  background: #f5f7fa !important;
}
.kb-list-container .header, .org-list-container .header,
.list-space-sidebar { background: #f5f7fa !important; }
.kb-list-content > .header, .org-list-content > .header,
.agent-list-content > .header { padding: 24px !important; }
.kb-list-main, .org-list-main { padding: 24px !important; }
.kb-list-content .header-title, .org-list-content .header-title,
.agent-list-content .header-title {
  font-size: 24px !important; line-height: 1.4 !important;
  font-weight: 650 !important; color: #20364d !important;
}
/* Remove only the list-page introductions; keep resource descriptions,
   form help, validation and permission notices in the native workspace. */
.kb-list-content > .header .header-subtitle,
.org-list-content > .header .header-subtitle,
.agent-list-content > .header .header-subtitle { display: none !important; }
.kb-card.kb-type-document, .kb-card.kb-type-faq, .org-card,
.shared-kb-card.kb-type-document, .shared-kb-card.kb-type-faq {
  background: #ffffff !important;
  border-color: #dce4ed !important;
  box-shadow: none !important;
}
.kb-card.kb-type-document:hover, .kb-card.kb-type-faq:hover,
.org-card:hover { border-color: #0067b1 !important; }
.kb-card.kb-type-document::after, .kb-card.kb-type-faq::after,
.org-card::before { content: none !important; }
.aside_box .menu_item_active { background: #eaf3fb !important; }
.kb-card .card-title-text { color: #20364d !important; }
.kb-card .card-description { color: #526579 !important; }
.t-button { min-height: 36px; border-radius: 10px !important; font-weight: 500; box-shadow: none !important; }
.t-button:focus-visible { outline: 2px solid #0067b1; outline-offset: 2px; }

@media (max-width: 767px) {
  html, body, #app { min-width: 0 !important; }
  #app .main { min-width: 0 !important; width: 100%; max-width: 100vw; position: relative; overflow: hidden; }
  .main > .aside_box {
    position: absolute !important; inset: 0 auto 0 0;
    z-index: 90; width: min(280px, 86vw) !important;
    min-width: 0 !important; max-width: 86vw;
    box-shadow: 8px 0 24px rgba(18, 47, 76, .12);
  }
  .main > .aside_box.aside_box--collapsed {
    width: 56px !important; min-width: 56px !important; box-shadow: none;
  }
  .main > .platform-route-outlet {
    margin-left: 56px; width: calc(100% - 56px); max-width: calc(100% - 56px); min-width: 0 !important;
  }
  .aside_box .sidebar-toggle, .aside_box .sidebar-toggle-item,
  .aside_box .menu_item { min-width: 44px; min-height: 44px; }
  .kb-list-container { min-width: 0 !important; width: 100%; flex-direction: column; }
  .kb-list-container, .kb-list-container * { box-sizing: border-box; }
  .kb-list-container > .list-space-sidebar {
    width: 100% !important; min-width: 0 !important; height: auto;
    max-height: 160px; flex: 0 0 auto; overflow: auto; scrollbar-width: none;
  }
  .kb-list-container > .list-space-sidebar .icon-strip {
    width: 100%; flex-direction: row; overflow-x: auto; padding: 8px; gap: 4px;
  }
  .list-space-sidebar .icon-item-labeled { min-width: 52px; min-height: 44px; }
  .kb-list-container > .list-space-sidebar::-webkit-scrollbar,
  .kb-list-container > .list-space-sidebar .icon-strip::-webkit-scrollbar { width: 0; height: 0; }
  .kb-list-content, .kb-list-main { min-width: 0 !important; width: 100% !important; }
  .kb-list-content { padding: 0 !important; }
  .kb-list-content > .header, .kb-list-main { padding: 16px !important; }
  .kb-list-content > .header, .org-list-content > .header,
  .kb-list-content .header-title, .org-list-content .header-title {
    min-width: 0 !important; max-width: 100%;
    font-size: 22px !important;
  }
  .kb-list-content .header-subtitle, .org-list-content .header-subtitle {
    white-space: normal !important; overflow-wrap: anywhere; max-width: 100%;
  }
  .kb-card-wrap { grid-template-columns: minmax(0, 1fr) !important; gap: 12px !important; }
  .kb-card { min-width: 0 !important; width: 100% !important; box-sizing: border-box; }
  .kb-card .card-title { min-width: 0; flex: 1; }
  .kb-card .card-title-text { white-space: normal !important; overflow-wrap: anywhere; }
  .kb-card .card-header { align-items: flex-start; }
  .org-list-container { min-width: 0 !important; width: 100%; flex-direction: column; }
  .org-list-container, .org-list-container * { box-sizing: border-box; }
  .org-list-container > .list-space-sidebar { width: 100% !important; min-width: 0; max-height: 160px; flex: 0 0 auto; overflow: auto; scrollbar-width: none; }
  .org-list-container > .list-space-sidebar .icon-strip { width: 100%; flex-direction: row; overflow-x: auto; padding: 8px; gap: 4px; }
  .org-list-content { min-width: 0 !important; width: 100%; padding: 0 !important; }
  .org-list-content > .header, .org-list-main { padding: 16px !important; }
  .org-card-wrap { grid-template-columns: minmax(0, 1fr) !important; gap: 12px !important; }
  .org-card { min-width: 0 !important; width: 100% !important; }
  .org-card .org-name, .org-card .card-title { white-space: normal !important; overflow-wrap: anywhere; }
  .t-dialog { width: calc(100vw - 24px) !important; max-width: calc(100vw - 24px) !important; max-height: calc(100vh - 24px); max-height: calc(100dvh - 24px); overflow-y: auto; }
  .t-dialog__body { min-width: 0; overflow-wrap: anywhere; }
  .t-dialog__footer { display: flex; flex-wrap: wrap; gap: 8px; }
  .t-dialog .t-dialog__close { min-width: 44px; min-height: 44px; }
  .doc-filter-bar { grid-template-columns: minmax(0, 1fr) auto; }
  .doc-filter-bar .doc-filter-bar__filters { gap: 8px; padding-bottom: 4px; overscroll-behavior-inline: contain; }
  .doc-filter-bar .doc-tag-filter-trigger, .doc-filter-bar .t-input { min-height: 44px; }
  .doc-filter-bar .doc-filter-field { width: 144px; }
  .doc-filter-bar .doc-filter-field--wide { width: 280px; }
  .upload-confirm-overlay .upload-confirm-modal {
    width: calc(100vw - 16px); max-width: calc(100vw - 16px);
    height: calc(100vh - 16px); height: calc(100dvh - 16px); max-height: 920px;
  }
  .upload-confirm-overlay .upload-confirm-modal .close-btn {
    top: 6px; right: 6px; width: 44px; height: 44px;
  }
  .upload-confirm-overlay .upload-confirm-modal .sidebar-header-row { box-sizing: border-box; width: 100%; min-width: 0; padding-right: 48px; gap: 8px; }
  .upload-confirm-overlay .upload-confirm-modal .sidebar-title { min-width: 0; overflow-wrap: anywhere; font-size: 16px; }
  .upload-confirm-overlay .upload-confirm-modal .files-panel { max-height: 220px; min-height: 160px; }
  .upload-confirm-overlay .upload-confirm-modal .files-list-wrap { min-height: 60px; overflow-y: auto; }
  .upload-confirm-overlay .upload-confirm-modal .file-name { white-space: normal; overflow-wrap: anywhere; }
  .upload-confirm-overlay .upload-confirm-modal .file-remove { width: 44px; height: 44px; flex-shrink: 0; }
  .upload-confirm-overlay .upload-confirm-modal .settings-sidebar-header { min-height: 40px; padding: 8px 12px; }
  .upload-confirm-overlay .upload-confirm-modal .settings-nav { overscroll-behavior-inline: contain; }
  .upload-confirm-overlay .upload-confirm-modal .config-panel { min-height: 0; min-width: 0; }
  .upload-confirm-overlay .upload-confirm-modal .upload-confirm-content { padding: 16px; overflow-wrap: anywhere; overscroll-behavior: contain; }
  .upload-confirm-overlay .upload-confirm-modal .setting-row { flex-direction: column; align-items: stretch; gap: 12px; }
  .upload-confirm-overlay .upload-confirm-modal .setting-info,
  .upload-confirm-overlay .upload-confirm-modal .setting-control { box-sizing: border-box; padding-right: 0; width: 100%; max-width: 100%; min-width: 0; flex: 0 0 auto; }
  .upload-confirm-overlay .upload-confirm-modal .modal-footer { flex-wrap: wrap; padding: 12px 16px max(12px, env(safe-area-inset-bottom)); gap: 8px; }
  .upload-destination-popup { max-width: calc(100vw - 24px); }
  .t-drawer__content-wrapper { max-width: 100vw !important; }
  .t-table__content { overflow-x: auto !important; -webkit-overflow-scrolling: touch; }
  /* Native knowledge/space settings use their own modal, not t-dialog. Keep
     every native section and action while moving the directory above the form. */
  .settings-overlay .settings-modal {
    width: calc(100vw - 24px) !important; max-width: none !important;
    height: calc(100vh - 24px) !important; height: calc(100dvh - 24px) !important;
    max-height: none !important; min-width: 0; border-radius: 12px;
  }
  .settings-overlay .settings-modal .settings-container { flex-direction: column; min-height: 0; }
  .settings-overlay .settings-modal .settings-sidebar {
    width: 100% !important; min-width: 0; flex: 0 0 auto;
    border-right: 0; border-bottom: 1px solid var(--td-component-stroke);
  }
  .settings-overlay .settings-modal .sidebar-header { min-height: 56px; padding: 16px 60px 12px 16px; }
  .settings-overlay .settings-modal .close-btn { top: 6px; right: 6px; width: 44px; height: 44px; }
  .settings-overlay .settings-modal .settings-nav {
    display: flex; flex: 0 0 auto; gap: 4px; padding: 4px 8px 8px;
    overflow-x: auto; overflow-y: hidden; overscroll-behavior-x: contain;
  }
  .settings-overlay .settings-modal .settings-nav > .nav-group-title { display: none; }
  .settings-overlay .settings-modal .nav-item { flex: 0 0 auto; min-height: 44px; margin: 0; white-space: nowrap; }
  .settings-overlay .settings-modal .settings-content { min-height: 0; min-width: 0; width: 100%; }
  .settings-overlay .settings-modal .content-wrapper { min-height: 0; min-width: 0; padding: 16px; overscroll-behavior: contain; }
  .settings-overlay .settings-modal .section-header { position: static; padding: 0; margin: 0 0 16px; flex-wrap: wrap; }
  .settings-overlay .settings-modal .setting-row { flex-direction: column; align-items: stretch; gap: 12px; }
  .settings-overlay .settings-modal .setting-row .setting-info, .settings-overlay .settings-modal .setting-row .setting-control {
    flex: 0 0 auto; width: 100%; min-width: 0; max-width: 100%; padding-right: 0;
  }
  .settings-overlay .settings-modal .setting-control { justify-content: flex-start; }
  .settings-overlay .settings-modal .indexing-checks { grid-template-columns: minmax(0, 1fr); }
  .settings-overlay .settings-modal .settings-footer {
    gap: 8px; flex-wrap: wrap; padding: 12px 16px max(12px, env(safe-area-inset-bottom));
  }
  .settings-overlay .settings-modal .settings-footer-actions { gap: 8px; max-width: 100%; flex-wrap: wrap; }
  .settings-overlay .settings-modal .t-input, .settings-overlay .settings-modal .t-select { min-width: 0; max-width: 100%; }
  .settings-overlay .settings-modal .t-input { min-height: 44px; }
  input, textarea, select { font-size: 16px !important; }
  .t-button { min-width: 44px; min-height: 44px; }
}
`;

export function applyWeKnoraMobileLayout(frame: HTMLIFrameElement): boolean {
  try {
    const document = frame.contentDocument;
    const location = frame.contentWindow?.location;
    // Never decorate a login redirect or an unrelated embedded destination.
    if (
      !document?.head ||
      location?.origin !== window.location.origin ||
      !location.pathname.startsWith("/platform/") ||
      location.pathname.startsWith("/platform/skillhub")
    ) {
      return false;
    }
    let style = document.getElementById(MOBILE_STYLE_ID);
    if (!style) {
      style = document.createElement("style");
      style.id = MOBILE_STYLE_ID;
      document.head.appendChild(style);
    }
    style.textContent = MOBILE_CSS;
    return true;
  } catch {
    // Same-origin deployment is required; leave unsupported frames unchanged.
    return false;
  }
}

/** Keep host navigation from covering native modal actions. Observe only the
 * local native frame and publish changes, not every native DOM mutation. */
export function observeWeKnoraModal(
  frame: HTMLIFrameElement,
  onChange: (open: boolean) => void,
): () => void {
  onChange(false);
  if (!applyWeKnoraMobileLayout(frame)) return () => undefined;
  const document = frame.contentDocument;
  if (!document?.body) return () => undefined;
  let lastOpen = false;
  let pending: ReturnType<typeof setTimeout> | undefined;
  const inspect = () => {
    pending = undefined;
    const open = Array.from(
      document.querySelectorAll<HTMLElement>(
        ".settings-overlay, .upload-confirm-overlay",
      ),
    ).some((element) => {
      const style = (document.defaultView ?? window).getComputedStyle(element);
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        element.getClientRects().length > 0
      );
    });
    if (open !== lastOpen) {
      lastOpen = open;
      onChange(open);
    }
  };
  const observer = new MutationObserver(() => {
    pending ??= setTimeout(inspect, 0);
  });
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["class", "style", "hidden"],
  });
  inspect();
  return () => {
    observer.disconnect();
    clearTimeout(pending);
  };
}

/** Use native controls after its asynchronous SSO/sidebar bootstrap has mounted.
 * Collapse once on mobile entry, then leave the user's menu choice alone.
 */
export function initializeWeKnoraMobileNavigation(
  frame: HTMLIFrameElement,
): () => void {
  if (!applyWeKnoraMobileLayout(frame)) return () => undefined;
  const document = frame.contentDocument;
  if (!document || !window.matchMedia("(max-width: 767px)").matches) {
    return () => undefined;
  }
  let disposed = false;
  let pending: ReturnType<typeof setTimeout> | undefined;
  const collapse = () => {
    const sidebar = document.querySelector(".main > .aside_box");
    if (!sidebar) return false;
    if (!sidebar.classList.contains("aside_box--collapsed")) {
      const toggle =
        sidebar.querySelector<HTMLButtonElement>(".sidebar-toggle");
      if (!toggle) return false;
      toggle.click();
      // Vue commits the class on its next render. Do not toggle again while
      // that render is pending or we would reopen the menu we just closed.
      return true;
    }
    return sidebar.classList.contains("aside_box--collapsed");
  };
  const initialize = () => {
    if (disposed || initializedMobileDocuments.has(document)) return;
    clearTimeout(pending);
    // Let native bootstrap finish its own first-entry expansion first.
    pending = setTimeout(() => {
      if (
        !disposed &&
        window.matchMedia("(max-width: 767px)").matches &&
        collapse()
      ) {
        initializedMobileDocuments.add(document);
        observer.disconnect();
      }
    }, 0);
  };
  const observer = new MutationObserver(initialize);
  if (!initializedMobileDocuments.has(document)) {
    observer.observe(document.body, { childList: true, subtree: true });
    initialize();
  }
  const deadline = setTimeout(() => observer.disconnect(), 15000);
  const onNavigate = (event: MouseEvent) => {
    const target = event.target as Element | null;
    if (
      !window.matchMedia("(max-width: 767px)").matches ||
      !target?.closest?.(".aside_box .menu_item") ||
      target.closest(".sidebar-toggle, .sidebar-toggle-item")
    )
      return;
    clearTimeout(pending);
    pending = setTimeout(() => {
      if (!disposed) collapse();
    }, 0);
  };
  document.addEventListener("click", onNavigate);
  return () => {
    disposed = true;
    clearTimeout(pending);
    clearTimeout(deadline);
    observer.disconnect();
    document.removeEventListener("click", onNavigate);
  };
}
