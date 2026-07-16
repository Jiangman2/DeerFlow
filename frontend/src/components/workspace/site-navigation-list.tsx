"use client";

import { ExternalLinkIcon, MapPinnedIcon } from "lucide-react";

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useSiteNavigation } from "@/core/site-navigation";

export function SiteNavigationList() {
  const { sites } = useSiteNavigation();

  if (sites.length === 0) {
    return null;
  }

  return (
    <SidebarGroup>
      <SidebarGroupLabel className="text-[#687386]">
        <MapPinnedIcon className="mr-1 size-4" />
        站点
      </SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {sites.slice(0, 12).map((site) => (
            <SidebarMenuItem key={site.id}>
              <SidebarMenuButton asChild tooltip={site.name}>
                <a
                  href={site.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-[#4f5967]"
                >
                  <ExternalLinkIcon className="text-[#8b939d]" />
                  <span>{site.name}</span>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
