import React, { useState } from 'react'
import { api } from '../../api/client'
import { useApi } from '../../hooks/useApi'
import { useNotifications } from '../../context/NotificationContext'
import { useAdmin } from '../../context/AdminContext'
import { formatTimestamp } from '../../utils/dates'
import type { NotificationRead } from '../../types'
import styles from './Notifications.module.css'

const TYPE_COLORS: Record<NotificationRead['type'], string> = {
  FOC_ALERT: '#58a6ff',
  RELEASE_ALERT: '#3fb950',
  REPRINT_ALERT: '#d29922',
  ARTIST_COVER_ALERT: '#bc8cff',
  SYNC_ERROR: '#f85149',
}

const TYPE_LABELS: Record<NotificationRead['type'], string> = {
  FOC_ALERT: 'FOC Alert',
  RELEASE_ALERT: 'Release',
  REPRINT_ALERT: 'Reprint',
  ARTIST_COVER_ALERT: 'Artist Cover',
  SYNC_ERROR: 'Sync Error',
}

export default function Notifications() {
  const { isAdmin } = useAdmin()
  const [filter, setFilter] = useState<'all' | 'unread'>('all')
  const { refresh } = useNotifications()
  const { data: syncLogs } = useApi(() => api.getSyncLogs())
  const lastSyncAt = syncLogs?.[0]?.started_at ?? null

  const { data: notifications, loading, refetch } = useApi(
    () => api.getNotifications(filter === 'unread'),
    [filter]
  )

  // "New" = created after the last sync, regardless of DB is_read state
  function isNew(n: NotificationRead): boolean {
    if (!lastSyncAt) return !n.is_read
    return new Date(n.created_at) >= new Date(lastSyncAt)
  }

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

  const unread = notifications?.filter(n => isNew(n)).length ?? 0

  return (
    <div>
      <div className={styles.pageHeader}>
        <h1>Notifications</h1>
        <div className={styles.actions}>
          {isAdmin && unread > 0 && (
            <button className={styles.btnSecondary} onClick={handleMarkAllRead}>
              Mark all read ({unread})
            </button>
          )}
          <div className={styles.filterGroup}>
            <button
              className={filter === 'all' ? styles.filterActive : styles.filter}
              onClick={() => setFilter('all')}
            >All</button>
            <button
              className={filter === 'unread' ? styles.filterActive : styles.filter}
              onClick={() => setFilter('unread')}
            >Unread</button>
          </div>
        </div>
      </div>

      <div className={styles.list}>
        {loading && <p className={styles.empty}>Loading…</p>}
        {!loading && (!notifications || notifications.length === 0) && (
          <p className={styles.empty}>
            {filter === 'unread' ? 'No unread notifications.' : 'No notifications yet.'}
          </p>
        )}
        {notifications?.map(n => (
          <div
            key={n.id}
            className={`${styles.item} ${isNew(n) ? styles.unread : ''}`}
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
              <p className={styles.itemTime}>{formatTimestamp(n.created_at)}</p>
            </div>
            <div className={styles.itemActions}>
              {isNew(n) && (
                <>
                  <span className={styles.dot} />
                  {isAdmin && !n.is_read && (
                    <button
                      className={styles.readBtn}
                      onClick={() => handleMarkRead(n.id)}
                    >
                      Mark read
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
