// API contract — mirrors backend/schemas.py
// Do not modify without also updating schemas.py

export interface TrackedArtist {
  name: string;
  locg_url: string | null;
}

export interface SeriesRead {
  id: number;
  name: string;
  locg_url: string | null;
  priority: string;
  is_followed: boolean;
  cover_image_url: string | null;
  locg_series_id: string | null;
  publisher: string | null;
  created_at: string;
}

export interface IssueCoverRead {
  id: number;
  cover_label: string | null;
  cover_image_url: string | null;
  artist_names: string[];
  locg_url: string | null;
}

export interface IssueRead {
  id: number;
  locg_issue_id: string | null;
  series_id: number;
  series_name: string;
  series_url: string | null;
  issue_number: string | null;
  title: string | null;
  release_date: string | null;
  foc_date: string | null;
  is_reprint: boolean;
  cover_image_url: string | null;
  locg_url: string | null;
  covers: IssueCoverRead[];
  has_tracked_artist: boolean;
}

export interface CoverVariantItem {
  label: string;
  locg_url: string | null;
  cover_image_url: string | null;
}

export interface FocExportRow {
  series_name: string;
  series_url: string | null;
  issue_number: string | null;
  foc_date: string | null;
  release_date: string | null;
  reprint_date: string | null;
  locg_url: string | null;
  cover_variants: CoverVariantItem[];
  has_tracked_artist: boolean;
  artist_names: string[];
}

export interface ArtistRead {
  id: number;
  name: string;
  locg_url: string | null;
  locg_creator_id: string | null;
  is_tracked: boolean;
}

export interface NotificationRead {
  id: number;
  type: 'FOC_ALERT' | 'RELEASE_ALERT' | 'REPRINT_ALERT' | 'ARTIST_COVER_ALERT' | 'COVER_UPDATE_ALERT' | 'SYNC_ERROR';
  title: string;
  body: string | null;
  issue_id: number | null;
  series_id: number | null;
  is_read: boolean;
  created_at: string;
}

export interface UnreadCountRead {
  count: number;
}

export interface NotificationSettingsRead {
  id: number;
  foc_alert_days: number;
  email_enabled: boolean;
  email_address: string | null;
  report_email: string | null;
  updated_at: string;
}

export interface NotificationSettingsUpdate {
  foc_alert_days?: number;
  email_enabled?: boolean;
  email_address?: string | null;
  report_email?: string | null;
}

export interface SyncLogRead {
  id: number;
  job_name: string;
  status: 'success' | 'error' | 'partial';
  records_fetched: number;
  records_inserted: number;
  error_message: string | null;
  error_detail: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface ReportRead {
  id: number;
  filename: string;
  generated_at: string;
  period_start: string | null;
  period_end: string | null;
}

export interface SyncNowResponse {
  message: string;
  job_id: string;
}
