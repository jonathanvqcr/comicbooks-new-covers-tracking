import React, { useState, useEffect } from 'react'
import { api } from '../../api/client'
import { useApi } from '../../hooks/useApi'
import { formatTimestamp } from '../../utils/dates'
import type { NotificationSettingsUpdate, SyncLogRead } from '../../types'
import styles from './Settings.module.css'

function StatusBadge({ status }: { status: SyncLogRead['status'] }) {
  const map = {
    success: styles.badgeGreen,
    error: styles.badgeRed,
    partial: styles.badgeAmber,
  }
  return <span className={`${styles.badge} ${map[status]}`}>{status}</span>
}

export default function Settings() {
  const { data: settings, loading: settingsLoading, refetch } = useApi(() =>
    api.getSettings()
  )
  const {
    data: syncLogs,
    loading: logsLoading,
    refetch: refetchLogs,
  } = useApi(() => api.getSyncLogs())

  const [form, setForm] = useState<NotificationSettingsUpdate>({})
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState('')

  useEffect(() => {
    if (settings) {
      setForm({
        foc_alert_days: settings.foc_alert_days,
        email_enabled: settings.email_enabled,
        email_address: settings.email_address ?? '',
        report_email: settings.report_email ?? '',
      })
    }
  }, [settings])

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setSaveMsg('')
    try {
      await api.updateSettings(form)
      refetch()
      setSaveMsg('Settings saved!')
      setTimeout(() => setSaveMsg(''), 3000)
    } catch {
      setSaveMsg('Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  async function handleSync() {
    setSyncing(true)
    setSyncMsg('')
    try {
      const r = await api.syncNow()
      setSyncMsg(r.message)
      setTimeout(() => {
        refetchLogs()
        setSyncMsg('')
      }, 3000)
    } catch {
      setSyncMsg('Sync request failed')
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div>
      <h1 className={styles.h1}>Settings</h1>

      {/* Notification settings */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Notifications</h2>
        {settingsLoading ? (
          <p className={styles.loading}>Loading…</p>
        ) : (
          <form onSubmit={handleSave} className={styles.form}>
            <div className={styles.field}>
              <label className={styles.label}>FOC alert window (days)</label>
              <input
                type="number"
                min={1}
                max={60}
                className={styles.input}
                value={form.foc_alert_days ?? 14}
                onChange={(e) =>
                  setForm((f) => ({ ...f, foc_alert_days: Number(e.target.value) }))
                }
              />
              <p className={styles.hint}>
                Get notified when FOC is within this many days
              </p>
            </div>

            <div className={styles.field}>
              <label className={styles.label}>
                <input
                  type="checkbox"
                  className={styles.checkbox}
                  checked={form.email_enabled ?? false}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, email_enabled: e.target.checked }))
                  }
                />
                Enable email notifications
              </label>
            </div>

            {form.email_enabled && (
              <>
                <div className={styles.field}>
                  <label className={styles.label}>
                    Notification email address
                  </label>
                  <input
                    type="email"
                    className={styles.input}
                    placeholder="you@example.com"
                    value={form.email_address ?? ''}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, email_address: e.target.value }))
                    }
                  />
                </div>
                <div className={styles.field}>
                  <label className={styles.label}>
                    Weekly report email (PDF recipient)
                  </label>
                  <input
                    type="email"
                    className={styles.input}
                    placeholder="you@example.com"
                    value={form.report_email ?? ''}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, report_email: e.target.value }))
                    }
                  />
                </div>
              </>
            )}

            <div className={styles.formActions}>
              {saveMsg && (
                <span
                  className={
                    saveMsg.includes('Failed')
                      ? styles.errorMsg
                      : styles.successMsg
                  }
                >
                  {saveMsg}
                </span>
              )}
              <button
                type="submit"
                className={styles.btnPrimary}
                disabled={saving}
              >
                {saving ? 'Saving…' : 'Save Settings'}
              </button>
            </div>
          </form>
        )}
      </section>

      {/* Manual sync */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Data Sync</h2>
        <p className={styles.sectionDesc}>
          LoCG data syncs automatically every Monday at 7am. Edit{' '}
          <code className={styles.code}>config/watchlist.yaml</code> to
          add/remove series and artists.
        </p>
        <div className={styles.syncRow}>
          <button
            className={styles.btnSecondary}
            onClick={handleSync}
            disabled={syncing}
          >
            {syncing ? '🔄 Syncing…' : '🔄 Sync Now'}
          </button>
          {syncMsg && <span className={styles.successMsg}>{syncMsg}</span>}
        </div>
      </section>

      {/* Sync log */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Sync Log</h2>
        {logsLoading && <p className={styles.loading}>Loading…</p>}
        {!logsLoading && (!syncLogs || syncLogs.length === 0) && (
          <p className={styles.empty}>No sync history yet.</p>
        )}
        {syncLogs && syncLogs.length > 0 && (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Job</th>
                  <th>Status</th>
                  <th>Fetched</th>
                  <th>New</th>
                  <th>Duration</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                {syncLogs.map((log) => {
                  const duration = log.finished_at
                    ? `${Math.round(
                        (new Date(log.finished_at).getTime() -
                          new Date(log.started_at).getTime()) /
                          1000
                      )}s`
                    : '—'
                  return (
                    <tr key={log.id}>
                      <td>{formatTimestamp(log.started_at)}</td>
                      <td>
                        <code className={styles.code}>{log.job_name}</code>
                      </td>
                      <td>
                        <StatusBadge status={log.status} />
                      </td>
                      <td>{log.records_fetched}</td>
                      <td>{log.records_inserted}</td>
                      <td>{duration}</td>
                      <td>
                        {log.error_message ? (
                          <span
                            className={styles.errorText}
                            title={log.error_detail ?? ''}
                          >
                            {log.error_message}
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
