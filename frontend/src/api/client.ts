import type {
  FocExportRow,
  IssueRead,
  NotificationRead,
  NotificationSettingsRead,
  NotificationSettingsUpdate,
  ReportRead,
  SyncLogRead,
  SyncNowResponse,
  UnreadCountRead,
} from '../types'

const BASE = '/api'
const FORCE_STATIC = import.meta.env.VITE_STATIC_MODE === 'true'

function isStatic(): boolean {
  if (FORCE_STATIC) return true
  return localStorage.getItem('comictracker_admin') !== 'on'
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`)
  return res.json()
}

async function getStatic<T>(file: string): Promise<T> {
  const res = await fetch(`/data/${file}`)
  if (!res.ok) throw new Error(`Static fetch ${file} → ${res.status}`)
  return res.json()
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`)
  return res.json()
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`PATCH ${path} → ${res.status}`)
  return res.json()
}

export const api = {
  // Issues
  getUpcomingIssues: () =>
    isStatic() ? getStatic<IssueRead[]>('upcoming-issues.json') : get<IssueRead[]>('/issues/upcoming'),
  getFocExport: () =>
    isStatic() ? getStatic<FocExportRow[]>('foc-export.json') : get<FocExportRow[]>('/issues/foc-export'),
  getReprints: () =>
    isStatic() ? getStatic<FocExportRow[]>('reprints.json') : get<FocExportRow[]>('/issues/reprints'),

  // Notifications
  getNotifications: (unreadOnly = false) =>
    isStatic()
      ? getStatic<NotificationRead[]>('notifications.json')
      : get<NotificationRead[]>(`/notifications${unreadOnly ? '?unread_only=true' : ''}`),
  getUnreadCount: (): Promise<UnreadCountRead> =>
    isStatic()
      ? Promise.resolve({ count: 0 })
      : get<UnreadCountRead>('/notifications/unread-count'),
  markRead: (id: number) => post<NotificationRead>(`/notifications/${id}/read`),
  markAllRead: () => post<{ message: string }>('/notifications/read-all'),

  // Settings
  getSettings: () => get<NotificationSettingsRead>('/settings'),
  updateSettings: (data: NotificationSettingsUpdate) =>
    patch<NotificationSettingsRead>('/settings', data),

  // Admin
  syncNow: () => post<SyncNowResponse>('/admin/sync-now'),
  getSyncLogs: (): Promise<SyncLogRead[]> =>
    isStatic()
      ? getStatic<SyncLogRead>('sync-info.json').then(d => [d])
      : get<SyncLogRead[]>('/admin/sync-log'),

  // Reports
  getReports: () => get<ReportRead[]>('/reports'),
  generateReport: () => post<{ message: string; job_id: string }>('/reports/generate-now'),
  getReportDownloadUrl: (id: number) => `${BASE}/reports/${id}/download`,
}
