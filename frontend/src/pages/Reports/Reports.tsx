import React, { useState } from 'react'
import { api } from '../../api/client'
import { useApi } from '../../hooks/useApi'
import styles from './Reports.module.css'

function formatDate(d: string): string {
  return new Date(d).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatPeriod(start: string | null, end: string | null): string {
  if (!start || !end) return ''
  const fmt = (d: string) =>
    new Date(d + 'T00:00:00').toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  return `${fmt(start)} – ${fmt(end)}`
}

export default function Reports() {
  const { data: reports, loading, refetch } = useApi(() => api.getReports())
  const [generating, setGenerating] = useState(false)
  const [genMsg, setGenMsg] = useState('')

  async function handleGenerate() {
    setGenerating(true)
    setGenMsg('')
    try {
      const r = await api.generateReport()
      setGenMsg(r.message + ' — refresh in a moment')
      setTimeout(() => {
        refetch()
        setGenMsg('')
      }, 4000)
    } catch {
      setGenMsg('Failed to start report generation')
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div>
      <div className={styles.pageHeader}>
        <h1>Reports</h1>
        <div className={styles.actions}>
          {genMsg && <span className={styles.genMsg}>{genMsg}</span>}
          <button
            className={styles.btnPrimary}
            onClick={handleGenerate}
            disabled={generating}
          >
            {generating ? 'Generating…' : '📄 Generate Now'}
          </button>
        </div>
      </div>

      <p className={styles.subtitle}>
        Weekly PDF reports are auto-generated every Friday at 9am.
        Download and share with your seller.
      </p>

      {loading && <p className={styles.empty}>Loading…</p>}
      {!loading && (!reports || reports.length === 0) && (
        <p className={styles.empty}>
          No reports yet. Generate one using the button above.
        </p>
      )}

      {reports && reports.length > 0 && (
        <div className={styles.grid}>
          {reports.map((r) => (
            <div key={r.id} className={styles.card}>
              <div className={styles.cardIcon}>📄</div>
              <div className={styles.cardBody}>
                <p className={styles.cardTitle}>{r.filename}</p>
                <p className={styles.cardMeta}>
                  Generated: {formatDate(r.generated_at)}
                </p>
                {(r.period_start || r.period_end) && (
                  <p className={styles.cardMeta}>
                    Period: {formatPeriod(r.period_start, r.period_end)}
                  </p>
                )}
              </div>
              <a
                href={api.getReportDownloadUrl(r.id)}
                download={r.filename}
                className={styles.downloadBtn}
              >
                ⬇ Download
              </a>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
