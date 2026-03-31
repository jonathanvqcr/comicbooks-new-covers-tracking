import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'

interface NotifCtx {
  unreadCount: number
  refresh: () => void
}

const NotificationContext = createContext<NotifCtx>({ unreadCount: 0, refresh: () => {} })

export function NotificationProvider({ children }: { children: React.ReactNode }) {
  const [unreadCount, setUnreadCount] = useState(0)

  const refresh = useCallback(() => {
    api.getUnreadCount().then(r => setUnreadCount(r.count)).catch(() => {})
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
