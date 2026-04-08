import React, { useState, useRef } from 'react'
import { api } from '../../api/client'
import { useApi } from '../../hooks/useApi'
import { useAdmin } from '../../context/AdminContext'
import { formatDate, formatTimestamp } from '../../utils/dates'
import type { FocExportRow, IssueRead } from '../../types'
import styles from './Dashboard.module.css'

function daysUntil(d: string | null): number | null {
  if (!d) return null
  const diff = new Date(d + 'T00:00:00').getTime() - Date.now()
  return Math.ceil(diff / 86400000)
}

function FocBadge({ date }: { date: string | null }) {
  const days = daysUntil(date)
  if (days === null) return <span className={styles.dash}>—</span>
  const cls = days <= 3 ? styles.urgentDate : days <= 7 ? styles.warnDate : styles.normalDate
  return (
    <span className={cls}>
      {formatDate(date)}
      <span className={styles.daysBadge}> ({days}d)</span>
    </span>
  )
}

function CoverStrip({ variants }: { variants: FocExportRow['cover_variants'] }) {
  if (!variants.length) return <span className={styles.dash}>—</span>
  return (
    <div className={styles.coverStrip}>
      {variants.map((cv, j) => {
        const inner = (
          <div className={styles.coverStripItem}>
            {cv.cover_image_url
              ? <img src={cv.cover_image_url} alt={cv.label} className={styles.coverStripThumb} />
              : <div className={styles.coverStripPlaceholder} />}
            <span className={styles.coverStripLabel}>{cv.label}</span>
          </div>
        )
        return cv.locg_url
          ? <a key={j} href={cv.locg_url} target="_blank" rel="noreferrer" className={styles.coverStripLink}>{inner}</a>
          : <div key={j}>{inner}</div>
      })}
    </div>
  )
}

// ── Monthly grouping ──────────────────────────────────────────────────────────

type MonthGroup = { label: string; sortKey: string; rows: FocExportRow[] }

function groupByFocMonth(rows: FocExportRow[]): MonthGroup[] {
  const groups = new Map<string, MonthGroup>()
  for (const row of rows) {
    const d = row.foc_date ? new Date(row.foc_date + 'T00:00:00') : null
    const sortKey = d
      ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
      : '9999-99'
    const label = d
      ? d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
      : 'No FOC Date'
    if (!groups.has(sortKey)) groups.set(sortKey, { label, sortKey, rows: [] })
    groups.get(sortKey)!.rows.push(row)
  }
  return Array.from(groups.values()).sort((a, b) => a.sortKey.localeCompare(b.sortKey))
}

function groupByReprintMonth(rows: FocExportRow[]): MonthGroup[] {
  const groups = new Map<string, MonthGroup>()
  for (const row of rows) {
    const d = row.reprint_date ? new Date(row.reprint_date + 'T00:00:00') : null
    const sortKey = d
      ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
      : '9999-99'
    const label = d
      ? d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
      : 'No Reprint Date'
    if (!groups.has(sortKey)) groups.set(sortKey, { label, sortKey, rows: [] })
    groups.get(sortKey)!.rows.push(row)
  }
  return Array.from(groups.values()).sort((a, b) => a.sortKey.localeCompare(b.sortKey))
}

// ── FOC table rows (shared between flat and monthly views) ────────────────────

function FocTableRows({ rows }: { rows: FocExportRow[] }) {
  return (
    <>
      {rows.map((row, i) => (
        <tr key={i} className={row.has_tracked_artist ? styles.trackedRow : ''}>
          <td>{row.has_tracked_artist ? '★ ' : ''}{row.series_name}</td>
          <td>
            {row.locg_url
              ? <a href={row.locg_url} target="_blank" rel="noreferrer" className={styles.issueLink}>
                  {row.issue_number ?? '—'}
                </a>
              : row.issue_number ?? '—'}
          </td>
          <td><FocBadge date={row.foc_date} /></td>
          <td><CoverStrip variants={row.cover_variants} /></td>
        </tr>
      ))}
    </>
  )
}

function FocCards({ rows }: { rows: FocExportRow[] }) {
  return (
    <>
      {rows.map((row, i) => (
        <div key={i} className={`${styles.focCard} ${row.has_tracked_artist ? styles.focCardTracked : ''}`}>
          <div className={styles.focCardHeader}>
            <span className={`${styles.focCardSeries} ${row.has_tracked_artist ? styles.focCardSeriesTracked : ''}`}>
              {row.has_tracked_artist ? '★ ' : ''}{row.series_name}
            </span>
            <div className={styles.focCardMeta}>
              {row.locg_url
                ? <a href={row.locg_url} target="_blank" rel="noreferrer" className={styles.issueLink}>#{row.issue_number ?? '—'}</a>
                : `#${row.issue_number ?? '—'}`}
              <FocBadge date={row.foc_date} />
            </div>
          </div>
          <CoverStrip variants={row.cover_variants} />
        </div>
      ))}
    </>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { isAdmin } = useAdmin()
  const [tab, setTab] = useState<'foc' | 'reprints' | 'artists'>('foc')
  const [copyMsg, setCopyMsg] = useState('')
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const { data: exportRows, loading: exportLoading, refetch: refetchExport } = useApi(() => api.getFocExport(), [isAdmin])
  const { data: reprintRows, loading: reprintsLoading, refetch: refetchReprints } = useApi(() => api.getReprints(), [isAdmin])
  const { data: artistAlerts, loading: artistLoading, refetch: refetchArtists } = useApi(() => api.getArtistAlerts(), [isAdmin])
  const { data: trackedArtists } = useApi(() => api.getTrackedArtists(), [isAdmin])
  const { data: syncLogs, refetch: refetchLogs } = useApi(() => api.getSyncLogs(), [isAdmin])

  const lastSync = syncLogs?.[0]
  const hasError = lastSync?.status === 'error'
  const artistIssues = artistAlerts ?? []

  // name → LoCG profile URL map for group header links
  const artistUrlMap: Map<string, string | null> = React.useMemo(() => {
    const map = new Map<string, string | null>()
    for (const a of trackedArtists ?? []) map.set(a.name, a.locg_url)
    return map
  }, [trackedArtists])

  // Group issues by artist name → { artistName: issues[] }
  const artistGroups: Map<string, IssueRead[]> = React.useMemo(() => {
    const map = new Map<string, IssueRead[]>()
    for (const issue of artistIssues) {
      const names = new Set(issue.covers.flatMap(c => c.artist_names))
      for (const name of names) {
        if (!map.has(name)) map.set(name, [])
        map.get(name)!.push(issue)
      }
    }
    return map
  }, [artistIssues])
  const monthGroups = exportRows ? groupByFocMonth(exportRows) : []
  const reprintMonthGroups = reprintRows ? groupByReprintMonth(reprintRows) : []

  React.useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  async function handleSync(type: 'all' | 'series' | 'reprints' | 'artists' = 'all') {
    setSyncing(true)
    setSyncMsg('Starting sync…')

    const apiFn = type === 'series' ? api.syncSeries
      : type === 'reprints' ? api.syncReprints
      : type === 'artists' ? api.syncArtists
      : api.syncNow

    let triggerTime: number
    try {
      triggerTime = Date.now()
      await apiFn()
    } catch {
      setSyncMsg('Sync failed — check settings')
      setSyncing(false)
      return
    }

    setSyncMsg('Syncing… (this may take a minute)')

    pollRef.current = setInterval(async () => {
      try {
        const logs = await api.getSyncLogs()
        const latest = logs?.[0]
        if (
          latest &&
          new Date(latest.started_at).getTime() >= triggerTime - 5000 &&
          latest.finished_at
        ) {
          clearInterval(pollRef.current!)
          pollRef.current = null
          refetchArtists()
          refetchExport()
          refetchReprints()
          refetchLogs()
          if (latest.status === 'error') {
            setSyncMsg(`❌ Sync failed: ${latest.error_message ?? 'unknown error'}`)
          } else {
            const label = latest.status === 'partial' ? '⚠ Partial sync' : '✓ Sync complete'
            const detail = latest.records_fetched > 0
              ? ` — ${latest.records_fetched} fetched, ${latest.records_inserted} new covers`
              : ''
            setSyncMsg(label + detail)
          }
          setSyncing(false)
          setTimeout(() => setSyncMsg(''), 6000)
        }
      } catch { /* ignore poll errors */ }
    }, 4000)
  }

  function handleCopy() {
    if (!exportRows) return

    let text: string
    const colHeader = 'Series\tIssue #\tFOC Date\tCover Variants'
    const toRow = (r: FocExportRow) =>
      [r.series_name, r.issue_number ?? '', r.foc_date ?? '', r.cover_variants.map(cv => cv.label).join(', ')].join('\t')

    if (tab === 'foc') {
      // Monthly grouped output with section headers
      const sections = monthGroups.map(g =>
        [`=== ${g.label} (${g.rows.length}) ===`, colHeader, ...g.rows.map(toRow)].join('\n')
      )
      text = sections.join('\n\n')
    } else {
      text = [colHeader, ...exportRows.map(toRow)].join('\n')
    }

    navigator.clipboard.writeText(text).then(() => {
      setCopyMsg('Copied!')
      setTimeout(() => setCopyMsg(''), 2000)
    })
  }

  return (
    <div>
      {hasError && (
        <div className={styles.errorBanner}>
          <strong>⚠ Last sync failed:</strong> {lastSync?.error_message ?? 'Unknown error'}
        </div>
      )}

      <div className={styles.pageHeader}>
        <div className={styles.pageHeaderLeft}>
          <h1>Dashboard</h1>
          {lastSync && (
            <span className={styles.lastSynced}>
              Last synced: {formatTimestamp(lastSync.started_at)} — {lastSync.status}
            </span>
          )}
        </div>
        <div className={styles.actions}>
          {syncMsg && <span className={styles.syncMsg}>{syncMsg}</span>}
          {isAdmin && (
            <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
              <button className={styles.btnPrimary} onClick={() => handleSync('all')} disabled={syncing}>
                {syncing ? 'Syncing…' : '🔄 Sync All'}
              </button>
              <button className={styles.btnSecondary} onClick={() => handleSync('series')} disabled={syncing} style={{ fontSize: '0.82rem', padding: '4px 10px' }}>
                Series
              </button>
              <button className={styles.btnSecondary} onClick={() => handleSync('reprints')} disabled={syncing} style={{ fontSize: '0.82rem', padding: '4px 10px' }}>
                Reprints
              </button>
              <button className={styles.btnSecondary} onClick={() => handleSync('artists')} disabled={syncing} style={{ fontSize: '0.82rem', padding: '4px 10px' }}>
                Artists
              </button>
            </div>
          )}
          <button className={styles.btnPrimary} onClick={handleCopy} disabled={!exportRows?.length}>
            {copyMsg || '📋 Copy'}
          </button>
        </div>
      </div>

      <div className={styles.tabs}>
        <button className={tab === 'foc' ? styles.tabActive : styles.tab} onClick={() => setTab('foc')}>
          FOC Calendar
          {exportRows && <span className={styles.count}>{exportRows.length}</span>}
        </button>
        <button className={tab === 'reprints' ? styles.tabActive : styles.tab} onClick={() => setTab('reprints')}>
          Reprints
          {reprintRows && reprintRows.length > 0 && <span className={styles.count}>{reprintRows.length}</span>}
        </button>
        <button className={tab === 'artists' ? styles.tabActive : styles.tab} onClick={() => setTab('artists')}>
          Artist Alerts
          {artistIssues.length > 0 && <span className={styles.countAlert}>{artistIssues.length}</span>}
        </button>
      </div>

      {/* ── FOC Calendar (grouped by month) ── */}
      {tab === 'foc' && (
        <>
          {exportLoading && <p className={styles.loading}>Loading…</p>}
          {!exportLoading && (!exportRows || exportRows.length === 0) && (
            <p className={styles.empty}>No upcoming FOC dates. Run a sync to fetch data.</p>
          )}

          {/* Desktop: table per month */}
          {monthGroups.length > 0 && (
            <div className={styles.monthlyDesktop}>
              {monthGroups.map(group => (
                <div key={group.sortKey} className={styles.monthSection}>
                  <div className={styles.monthHeader}>
                    <span className={styles.monthLabel}>{group.label}</span>
                    <span className={styles.monthCount}>{group.rows.length} issue{group.rows.length !== 1 ? 's' : ''}</span>
                  </div>
                  <div className={styles.tableWrap}>
                    <table className={styles.table}>
                      <thead><tr><th>Series</th><th>Issue #</th><th>FOC Date</th><th>Cover Variants</th></tr></thead>
                      <tbody><FocTableRows rows={group.rows} /></tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Mobile: cards per month */}
          {monthGroups.length > 0 && (
            <div className={styles.monthlyMobile}>
              {monthGroups.map(group => (
                <div key={group.sortKey} className={styles.monthSection}>
                  <div className={styles.monthHeader}>
                    <span className={styles.monthLabel}>{group.label}</span>
                    <span className={styles.monthCount}>{group.rows.length} issue{group.rows.length !== 1 ? 's' : ''}</span>
                  </div>
                  <div className={styles.monthCardList}>
                    <FocCards rows={group.rows} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* ── Artist Alerts ── */}
      {tab === 'artists' && (
        <>
          {artistLoading && <p className={styles.loading}>Loading…</p>}
          {!artistLoading && artistIssues.length === 0 && (
            <p className={styles.empty}>No upcoming issues with tracked artists found.</p>
          )}

          {/* Desktop: one table per artist */}
          {artistGroups.size > 0 && (
            <div className={`${styles.tableWrap} ${styles.artistTableWrap}`}>
              {[...artistGroups.entries()].map(([artistName, groupIssues]) => (
                <div key={artistName} className={styles.artistGroup}>
                  <h3 className={styles.artistGroupHeader}>
                    {artistUrlMap.get(artistName)
                      ? <a href={artistUrlMap.get(artistName)!} target="_blank" rel="noreferrer" className={styles.artistGroupLink}>{artistName}</a>
                      : artistName}
                  </h3>
                  <table className={styles.table}>
                    <thead>
                      <tr>
                        <th>Series</th><th>Issue #</th><th>FOC Date</th>
                        <th>Release Date</th><th>Covers</th>
                      </tr>
                    </thead>
                    <tbody>
                      {groupIssues.map(issue => {
                        const artistCovers = issue.covers.filter(c => c.artist_names.includes(artistName))
                        return (
                          <tr key={issue.id}>
                            <td>{issue.series_name}</td>
                            <td>{issue.issue_number ?? '—'}</td>
                            <td><FocBadge date={issue.foc_date} /></td>
                            <td>{formatDate(issue.release_date)}</td>
                            <td>
                              <div className={styles.coverList}>
                                {artistCovers.map(c => {
                                  const content = (
                                    <>
                                      {c.cover_image_url && <img src={c.cover_image_url} alt={c.cover_label ?? 'Cover'} className={styles.coverThumb} />}
                                      <span className={styles.coverLabel}>{c.cover_label ?? 'Cover'}</span>
                                    </>
                                  )
                                  return (
                                    <div key={c.id} className={styles.coverItem}>
                                      {c.locg_url
                                        ? <a href={c.locg_url} target="_blank" rel="noreferrer" className={styles.coverItemLink}>{content}</a>
                                        : content}
                                    </div>
                                  )
                                })}
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          )}

          {/* Mobile: grouped cards */}
          {artistGroups.size > 0 && (
            <div className={styles.artistCardList}>
              {[...artistGroups.entries()].map(([artistName, groupIssues]) => (
                <div key={artistName} className={styles.artistGroup}>
                  <h3 className={styles.artistGroupHeader}>
                    {artistUrlMap.get(artistName)
                      ? <a href={artistUrlMap.get(artistName)!} target="_blank" rel="noreferrer" className={styles.artistGroupLink}>{artistName}</a>
                      : artistName}
                  </h3>
                  {groupIssues.map(issue => {
                    const artistCovers = issue.covers.filter(c => c.artist_names.includes(artistName))
                    return (
                      <div key={issue.id} className={styles.artistCard}>
                        <div className={styles.focCardHeader}>
                          <span className={styles.focCardSeries}>{issue.series_name}</span>
                          <div className={styles.focCardMeta}>
                            <span>#{issue.issue_number ?? '—'}</span>
                            <FocBadge date={issue.foc_date} />
                          </div>
                        </div>
                        <div className={styles.coverList}>
                          {artistCovers.map(c => {
                            const content = (
                              <>
                                {c.cover_image_url && <img src={c.cover_image_url} alt={c.cover_label ?? 'Cover'} className={styles.coverThumb} />}
                                <span className={styles.coverLabel}>{c.cover_label ?? 'Cover'}</span>
                              </>
                            )
                            return (
                              <div key={c.id} className={styles.coverItem}>
                                {c.locg_url
                                  ? <a href={c.locg_url} target="_blank" rel="noreferrer" className={styles.coverItemLink}>{content}</a>
                                  : content}
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* ── Reprints ── */}
      {tab === 'reprints' && (
        <>
          {reprintsLoading && <p className={styles.loading}>Loading…</p>}
          {!reprintsLoading && (!reprintRows || reprintRows.length === 0) && (
            <p className={styles.empty}>No upcoming reprints found. Run a sync to check for announced reprints.</p>
          )}

          {/* Desktop: table per month */}
          {reprintMonthGroups.length > 0 && (
            <div className={styles.monthlyDesktop}>
              {reprintMonthGroups.map(group => (
                <div key={group.sortKey} className={styles.monthSection}>
                  <div className={styles.monthHeader}>
                    <span className={styles.monthLabel}>{group.label}</span>
                    <span className={styles.monthCount}>{group.rows.length} reprint{group.rows.length !== 1 ? 's' : ''}</span>
                  </div>
                  <div className={styles.tableWrap}>
                    <table className={styles.table}>
                      <thead><tr><th>Series</th><th>Issue #</th><th>Reprint Date</th><th>Original Release</th><th>Cover</th></tr></thead>
                      <tbody>
                        {group.rows.map((row, i) => (
                          <tr key={i}>
                            <td>{row.series_name}</td>
                            <td>
                              {row.locg_url
                                ? <a href={row.locg_url} target="_blank" rel="noreferrer" className={styles.issueLink}>{row.issue_number ?? '—'}</a>
                                : row.issue_number ?? '—'}
                            </td>
                            <td>{formatDate(row.reprint_date)}</td>
                            <td>{formatDate(row.release_date)}</td>
                            <td><CoverStrip variants={row.cover_variants} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Mobile: cards per month */}
          {reprintMonthGroups.length > 0 && (
            <div className={styles.monthlyMobile}>
              {reprintMonthGroups.map(group => (
                <div key={group.sortKey} className={styles.monthSection}>
                  <div className={styles.monthHeader}>
                    <span className={styles.monthLabel}>{group.label}</span>
                    <span className={styles.monthCount}>{group.rows.length} reprint{group.rows.length !== 1 ? 's' : ''}</span>
                  </div>
                  <div className={styles.monthCardList}>
                    {group.rows.map((row, i) => (
                      <div key={i} className={styles.focCard}>
                        <div className={styles.focCardHeader}>
                          <span className={styles.focCardSeries}>{row.series_name}</span>
                          <div className={styles.focCardMeta}>
                            {row.locg_url
                              ? <a href={row.locg_url} target="_blank" rel="noreferrer" className={styles.issueLink}>#{row.issue_number ?? '—'}</a>
                              : `#${row.issue_number ?? '—'}`}
                            <span className={styles.normalDate}>{formatDate(row.reprint_date)}</span>
                            {row.release_date && <span className={styles.normalDate} style={{opacity: 0.6, fontSize: '0.78rem'}}>orig. {formatDate(row.release_date)}</span>}
                          </div>
                        </div>
                        <CoverStrip variants={row.cover_variants} />
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

    </div>
  )
}
