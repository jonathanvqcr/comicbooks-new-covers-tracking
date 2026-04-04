const TZ = 'America/Costa_Rica'

/** Format a full ISO timestamp (e.g. created_at, started_at) in Costa Rica time */
export function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    timeZone: TZ,
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Format a date-only string (e.g. foc_date: "2026-04-06") — no timezone needed */
export function formatDate(d: string | null): string {
  if (!d) return '—'
  return new Date(d + 'T00:00:00').toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}
