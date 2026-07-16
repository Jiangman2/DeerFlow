export type AnnouncementSeverity = "info" | "success" | "warning" | "critical";
export type AnnouncementStatus = "draft" | "published" | "archived";

export type Announcement = {
  id: string;
  title: string;
  content: string;
  severity: AnnouncementSeverity;
  status: AnnouncementStatus;
  dismissible: boolean;
  starts_at: string | null;
  ends_at: string | null;
  created_at: string;
  updated_at: string;
  created_by_user_id: string | null;
  created_by_email: string | null;
  updated_by_user_id: string | null;
  updated_by_email: string | null;
};

export type AnnouncementInput = {
  title: string;
  content: string;
  severity: AnnouncementSeverity;
  status: AnnouncementStatus;
  dismissible: boolean;
  starts_at?: string | null;
  ends_at?: string | null;
};
