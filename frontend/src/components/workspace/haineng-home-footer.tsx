"use client";

import { SquarePenIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";

type Counts = {
  todayModelCallCount: number;
  totalModelCallCount: number;
};

export function HainengHomeFooter() {
  const { t } = useI18n();
  const [counts, setCounts] = useState<Counts | null>(null);

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

    void loadCounts();
    const interval = window.setInterval(() => void loadCounts(), 60_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const formatCount = (value: number | undefined) =>
    value === undefined ? "—" : new Intl.NumberFormat("zh-CN").format(value);

  return (
    <footer
      aria-label="版权信息"
      className="cnooc-welcome-copyright cnooc-home-footer"
    >
      <span className="cnooc-home-copyright-text">
        Copyright © 2026-2027 中海油研究总院有限责任公司 版权所有
      </span>
      <span className="cnooc-home-address">
        地址：北京市朝阳区太阳宫南街6号院
      </span>
      <div className="cnooc-home-footer-tools">
        <div
          aria-live="polite"
          className="cnooc-home-usage"
          title={counts ? undefined : t.welcome.usageUnavailable}
        >
          <span>
            {t.welcome.totalUsage}：
            <strong>{formatCount(counts?.totalModelCallCount)}</strong>
          </span>
          <span>
            {t.welcome.todayUsage}：
            <strong>{formatCount(counts?.todayModelCallCount)}</strong>
          </span>
        </div>
        <div className="cnooc-home-contact">
          <button type="button">{t.welcome.contactUs}</button>
          <div className="cnooc-home-contact-card">
            <div className="cnooc-home-contact-card-header">
              <strong>{t.welcome.contactUs}</strong>
              <a href="mailto:shiyl19@cnooc.com.cn?subject=%E6%84%8F%E8%A7%81%E5%8F%8D%E9%A6%88">
                <SquarePenIcon aria-hidden="true" />
                {t.welcome.feedback}
              </a>
            </div>
            <dl className="cnooc-home-contact-details">
              <div>
                <dt>{t.welcome.supportPhone}：</dt>
                <dd>
                  <a href="tel:18810388532">18810388532</a>
                </dd>
              </div>
              <div>
                <dt>{t.welcome.supportEmail}：</dt>
                <dd>
                  <a href="mailto:shiyl19@cnooc.com.cn">shiyl19@cnooc.com.cn</a>
                </dd>
              </div>
              <div>
                <dt>{t.welcome.businessOwner}：</dt>
                <dd>姜彬</dd>
              </div>
              <div>
                <dt>{t.welcome.operationsOwner}：</dt>
                <dd>姜曼</dd>
              </div>
              <div>
                <dt>{t.welcome.serviceHours}：</dt>
                <dd>8:30 ～ 17:30</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </footer>
  );
}
