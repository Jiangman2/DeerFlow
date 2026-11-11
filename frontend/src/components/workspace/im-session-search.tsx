"use client";

import { Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";
import { imSessionCopy } from "@/core/i18n/im-session-tree";
import { useImThreadSearch } from "@/core/im/hooks";
import { threadDisplayTitle } from "@/core/im/tree";
import { pathOfThread } from "@/core/threads/utils";

const SEARCH_DEBOUNCE_MS = 250;

export function ImSessionSearch() {
  const { locale } = useI18n();
  const copy = imSessionCopy(locale);
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(draft.trim());
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft]);

  const { data, isFetching } = useImThreadSearch(query);
  const results = data?.results ?? [];
  const truncated = data !== undefined && data.results.length >= data.limit;

  return (
    <div className="flex flex-col gap-1 px-2 py-1">
      <div className="relative">
        <Search
          aria-hidden="true"
          className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2"
        />
        <Input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={copy.imSearchPlaceholder}
          aria-label={copy.imSearchPlaceholder}
          className="h-7 pl-7 text-xs"
          data-testid="im-session-search-input"
        />
      </div>
      {query.length > 0 && (
        <div data-testid="im-session-search-results">
          {results.length === 0 && !isFetching && (
            <p className="text-muted-foreground px-1 py-1 text-[11px]">
              {copy.imSearchNoResult}
            </p>
          )}
          {results.length > 0 && (
            <SidebarMenu>
              <div className="flex w-full flex-col gap-1">
                {results.map((thread) => (
                  <SidebarMenuItem key={thread.thread_id}>
                    <SidebarMenuButton asChild>
                      <Link
                        href={pathOfThread(thread.thread_id)}
                        className="text-muted-foreground min-w-0 whitespace-nowrap"
                      >
                        <span className="min-w-0 truncate">
                          {threadDisplayTitle(thread)}
                        </span>
                        <span className="bg-muted text-muted-foreground ml-auto inline-flex h-4 max-w-16 shrink-0 items-center rounded px-1 text-[10px]">
                          <span className="truncate">
                            {(thread.chat_name ?? "")
                              .toLowerCase()
                              .includes(query.toLowerCase())
                              ? copy.imMatchGroup
                              : copy.imMatchThread}
                          </span>
                        </span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </div>
            </SidebarMenu>
          )}
          {truncated && (
            <p className="text-muted-foreground px-1 py-1 text-[10px] leading-snug">
              {copy.imSearchTruncated}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
