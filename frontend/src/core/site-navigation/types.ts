export type SiteNavigationItem = {
  id: string;
  name: string;
  url: string;
  aliases: string[];
  description: string;
  category: string;
  sort_order: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  created_by_user_id: string | null;
  created_by_email: string | null;
  updated_by_user_id: string | null;
  updated_by_email: string | null;
};

export type SiteNavigationInput = {
  name: string;
  url: string;
  aliases: string[];
  description: string;
  category: string;
  sort_order: number;
  enabled: boolean;
};
