"use client";

import { SquarePenIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";

type Counts = {
  todayModelCallCount: number;
  totalModelCallCount: number;
};

type ActivityCounts = { todayUsers: number; todayTasks: number };
const SUPPORT_EMAIL = "sh_tyznt@cnooc.com.cn";
const PLATFORM_EMAIL = "sh_ai@cnooc.com.cn";

export function HainengHomeFooter() {
  const { t } = useI18n();
  const [counts, setCounts] = useState<Counts | null>(null);
  const [activity, setActivity] = useState<ActivityCounts | null>(null);

  useEffect(() => {
    let active = true;

    const loadCounts = async () => {
      try {
        const response = await fetch("/integrations/haineng/model-call-count", {
          cache: "no-store",
          credentials: "same-origin",
        });
        if (!response.ok) throw new Error("Usage unavailable");
        const result = (await response.json()) as Counts;
        if (active) setCounts(result);
      } catch {
        if (active) setCounts(null);
      }
    };

    const loadActivity = async () => {
      try {
        const response = await fetch("/api/home/statistics", {
          cache: "no-store",
          credentials: "same-origin",
        });
        if (!response.ok) throw new Error("Activity unavailable");
        const result = (await response.json()) as ActivityCounts;
        if (active) setActivity(result);
      } catch {
        if (active) setActivity(null);
      }
    };

    void loadCounts();
    void loadActivity();
    const interval = window.setInterval(() => {
      void loadCounts();
      void loadActivity();
    }, 60_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const formatCount = (value: number | undefined) =>
    value === undefined || !Number.isFinite(value) || value < 0
      ? "—"
      : new Intl.NumberFormat("zh-CN").format(value);

  return (
    <footer
      aria-label="版权信息"
      className="cnooc-welcome-copyright cnooc-home-footer"
    >
      <span className="cnooc-home-copyright-text">
        Copyright © 2024–2026 CNOOC. All rights
        reserved.中海油研究总院/中国海油集团信息技术中心版权所有
      </span>
      <div className="cnooc-home-footer-tools">
        <div
          aria-live="polite"
          className="cnooc-home-usage"
          title={counts && activity ? undefined : t.welcome.usageUnavailable}
        >
          <span>
            {t.welcome.totalUsage}：
            <strong>{formatCount(counts?.totalModelCallCount)}</strong>
          </span>
          <span>
            {t.welcome.todayUsage}：
            <strong>{formatCount(counts?.todayModelCallCount)}</strong>
          </span>
          <span>
            {t.welcome.todayUsers}：
            <strong>{formatCount(activity?.todayUsers)}</strong>
          </span>
          <span>
            {t.welcome.todayTasks}：
            <strong>{formatCount(activity?.todayTasks)}</strong>
          </span>
        </div>
        <div className="cnooc-home-contact">
          <button type="button">{t.welcome.contactUs}</button>
          <div className="cnooc-home-contact-card">
            <div className="cnooc-home-contact-card-header">
              <strong>{t.welcome.contactUs}</strong>
              <a
                href={`mailto:${SUPPORT_EMAIL}?subject=%E6%84%8F%E8%A7%81%E5%8F%8D%E9%A6%88`}
              >
                <SquarePenIcon aria-hidden="true" />
                {t.welcome.feedback}
              </a>
            </div>
            <dl className="cnooc-home-contact-details">
              <div>
                <dt>{t.welcome.generalAgentEmail}：</dt>
                <dd>
                  <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
                </dd>
              </div>
              <div>
                <dt>{t.welcome.aiPlatformEmail}：</dt>
                <dd>
                  <a href={`mailto:${PLATFORM_EMAIL}`}>{PLATFORM_EMAIL}</a>
                </dd>
              </div>
              <div>
                <dt>{t.welcome.supportPhone}：</dt>
                <dd className="cnooc-home-contact-phones">
                  <a href="tel:01084521314">010-84521314</a>
                  <span>，</span>
                  <a href="tel:13520340738">13520340738</a>
                </dd>
              </div>
              <div>
                <dt>{t.welcome.serviceHours}：</dt>
                <dd>8:30 ～ 17:30</dd>
              </div>
            </dl>
            <section className="cnooc-home-support-group">
              <strong>{t.welcome.supportGroupTitle}</strong>
              <p>{t.welcome.supportGroupName}</p>
              <div className="cnooc-home-support-qr">
                {/* Preserve the supplied QR pixels; do not optimize or regenerate. */}
                <img
                  src="/images/haineng-work-support-qr-20261012.jpg"
                  width={120}
                  height={120}
                  alt={t.welcome.supportGroupQrAlt}
                />
                <span>{t.welcome.supportGroupScan}</span>
              </div>
            </section>
          </div>
        </div>
      </div>
    </footer>
  );
}
