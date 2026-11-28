import {
  ArrowRightIcon,
  BookOpenTextIcon,
  FileSearchIcon,
  FileTextIcon,
  GitCompareArrowsIcon,
  LayoutGridIcon,
  ScanSearchIcon,
  ShieldCheckIcon,
  SquarePenIcon,
  type LucideIcon,
} from "lucide-react";
import { type Metadata } from "next";
import Image from "next/image";
import Link from "next/link";

import { NonConfidentialNotice } from "@/components/workspace/non-confidential-notice";
import { detectLocaleServer } from "@/core/i18n/server";
import { standardsPortalCopy } from "@/core/i18n/standards-portal";
import {
  buildStandardsAgentHref,
  parseStandardsPortalConfig,
  STANDARDS_MODULE_KEYS,
  type StandardsModuleKey,
} from "@/core/standards-portal/config";

import { StandardsPortalEntry } from "./portal-entry";

import styles from "./standards.module.css";

const moduleIcons: Record<StandardsModuleKey, LucideIcon> = {
  qa: BookOpenTextIcon,
  search: FileSearchIcon,
  policy: FileTextIcon,
  comparison: GitCompareArrowsIcon,
  applicability: ScanSearchIcon,
  compliance: ShieldCheckIcon,
  authoring: SquarePenIcon,
};

export async function generateMetadata(): Promise<Metadata> {
  const copy = standardsPortalCopy(await detectLocaleServer());
  return { title: copy.title, description: copy.description };
}

export default async function StandardsPage() {
  const copy = standardsPortalCopy(await detectLocaleServer());
  // Server runtime configuration stays independent of the built frontend image.
  const config = parseStandardsPortalConfig(
    process.env.STANDARDS_PORTAL_AGENTS_JSON,
  );
  if (config.issues.length) {
    // Diagnostics contain fixed issue codes/module keys, never raw input values.
    console.warn(
      "[SeaStandard] Module configuration needs attention",
      config.issues,
    );
  }

  function renderEntry(moduleKey: StandardsModuleKey, wide = false) {
    const feature = copy.features[moduleKey];
    const Icon = moduleIcons[moduleKey];
    const agentName = config.modules[moduleKey];
    const href = agentName ? buildStandardsAgentHref(agentName) : undefined;

    return (
      <StandardsPortalEntry
        className={wide ? styles.futureFeature : styles.featureCard}
        closeLabel={copy.closeDialog}
        href={href}
        key={moduleKey}
        moduleKey={moduleKey}
        title={feature.title}
        unconfiguredMessage={copy.unconfiguredMessage}
      >
        <span className={styles.iconBox}>
          <Icon aria-hidden="true" size={24} strokeWidth={1.7} />
        </span>
        <span className={wide ? styles.futureContent : styles.cardContent}>
          <span className={styles.cardTitle}>{feature.title}</span>
          <span className={styles.cardDescription}>{feature.description}</span>
        </span>
        <span className={styles.cardAction}>
          {copy.entryAction}
          <ArrowRightIcon aria-hidden="true" size={15} />
        </span>
      </StandardsPortalEntry>
    );
  }

  return (
    <div className={styles.portal} data-testid="seastandard-portal">
      <div className={styles.pageFrame}>
        <header className={styles.header}>
          <div className={styles.brandline}>
            <Image
              alt=""
              aria-hidden="true"
              className={styles.brandMark}
              height={128}
              priority
              src="/images/cnooc-haineng-mark.png"
              width={128}
            />
            <strong>{copy.brandSlogan}</strong>
            <span className={styles.brandDivider} aria-hidden="true" />
            <span className={styles.brandSubline}>{copy.platformName}</span>
          </div>
          <Link
            className={styles.backLink}
            href="/workspace/chats/new"
            prefetch={false}
          >
            <LayoutGridIcon aria-hidden="true" size={16} />
            <span>{copy.backToPlatform}</span>
            <ArrowRightIcon aria-hidden="true" size={15} />
          </Link>
        </header>

        <NonConfidentialNotice className={styles.notice} />

        <main className={styles.main}>
          <div className={styles.hero}>
            <span className={styles.eyebrow}>{copy.eyebrow}</span>
            <h1>{copy.title}</h1>
          </div>

          <section aria-labelledby="capabilities-title">
            <div className={styles.sectionHeading}>
              <h2 id="capabilities-title">{copy.capabilitiesTitle}</h2>
            </div>
            <p className={styles.conversationHint}>{copy.entryHint}</p>

            <div className={styles.featureGrid}>
              {STANDARDS_MODULE_KEYS.filter((key) => key !== "authoring").map(
                (key) => renderEntry(key),
              )}
            </div>

            {renderEntry("authoring", true)}
          </section>
        </main>

        <footer className={styles.footer}>
          <span>{copy.footer}</span>
          <span>{copy.copyright}</span>
        </footer>
      </div>
    </div>
  );
}
