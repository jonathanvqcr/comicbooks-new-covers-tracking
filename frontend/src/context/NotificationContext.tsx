import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'

interface NotifCtx {
  unreadCount: number
  refresh: () => void
}

const NotificationContext = createContext<NotifCtx>({ unreadCount: 0, refresh: () => {} })

export function NotificationProvider({ children }: { children: React.ReactNode }) {
  const [unreadCount, setUnreadCount] = useState(0)

  const refresh = useCallback(async () => {
    try {
      // Count notifications created after the last sync (consistent with the page display)
      const [logs, notifs] = await Promise.all([api.getSyncLogs(), api.getNotifications(false)])
      const lastSyncAt = logs?.[0]?.started_at ?? null
      if (lastSyncAt) {
        const count = notifs.filter(n => new Date(n.created_at) >= new Date(lastSyncAt)).length
        setUnreadCount(count)
      } else {
        setUnreadCount(notifs.filter(n => !n.is_read).length)
      }
    } catch { setUnreadCount(0) }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 60_000)
    return () => clearInterval(id)
  }, [refresh])

  return (
    <NotificationContext.Provider value={{ unreadCount, refresh }}>
      {children}
    </NotificationContext.Provider>
  )
}

export function useNotifications() {
  return useContext(NotificationContext)
}
