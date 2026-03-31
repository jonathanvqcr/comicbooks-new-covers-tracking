import React, { useEffect } from 'react'
import { api } from '../../api/client'
import { useApi } from '../../hooks/useApi'
import { useNotifications } from '../../context/NotificationContext'
import type { NotificationRead } from '../../types'
import styles from './NotificationPanel.module.css'

const TYPE_COLORS: Record<NotificationRead['type'], string> = {
  FOC_ALERT: '#58a6ff',
  RELEASE_ALERT: '#3fb950',
  REPRINT_ALERT: '#d29922',
  ARTIST_COVER_ALERT: '#bc8cff',
  SYNC_ERROR: '#f85149',
}

const TYPE_LABELS: Record<NotificationRead['type'], string> = {
  FOC_ALERT: 'FOC',
  RELEASE_ALERT: 'Release',
  REPRINT_ALERT: 'Reprint',
  ARTIST_COVER_ALERT: 'Artist',
  SYNC_ERROR: 'Error',
}

export default function NotificationPanel({ onClose }: { onClose: () => void }) {
  const { refresh } = useNotifications()
  const { data: notifications, loading, refetch } = useApi(() => api.getNotifications(false))

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  async function handleMarkAllRead() {
    await api.markAllRead()
    refetch()
    refresh()
  }

  async function handleMarkRead(id: number) {
    await api.markRead(id)
    refetch()
    refresh()
  }

  return (
    <>
      <div className={styles.backdrop} onClick={onClose} />
      <aside className={styles.panel}>
        <div className={styles.panelHeader}>
          <h2>Notifications</h2>
          <button className={styles.markAll} onClick={handleMarkAllRead}>Mark all read</button>
          <button className={styles.close} onClick={onClose}>✕</button>
        </div>
        <div className={styles.list}>
          {loading && <p className={styles.empty}>Loading…</p>}
          {!loading && (!notifications || notifications.length === 0) && (
            <p className={styles.empty}>No notifications yet.</p>
          )}
          {notifications?.map(n => (
            <div
              key={n.id}
              className={`${styles.item} ${!n.is_read ? styles.unread : ''}`}
              onClick={() => !n.is_read && handleMarkRead(n.id)}
            >
              <span
                className={styles.tag}
                style={{ background: TYPE_COLORS[n.type] + '22', color: TYPE_COLORS[n.type] }}
              >
                {TYPE_LABELS[n.type]}
              </span>
              <div className={styles.itemBody}>
                <p className={styles.itemTitle}>{n.title}</p>
                {n.body && <p className={styles.itemText}>{n.body}</p>}
                <p className={styles.itemTime}>{new Date(n.created_at).toLocaleString()}</p>
              </div>
              {!n.is_read && <span className={styles.dot} />}
            </div>
          ))}
        </div>
      </aside>
    </>
  )
}
