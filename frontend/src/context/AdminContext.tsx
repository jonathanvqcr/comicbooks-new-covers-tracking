import React, { createContext, useContext, useEffect, useState } from 'react'

const FORCE_STATIC = import.meta.env.VITE_STATIC_MODE === 'true'
const STORAGE_KEY = 'comictracker_admin'

interface AdminContextType {
  isAdmin: boolean
}

const AdminContext = createContext<AdminContextType>({ isAdmin: false })

export function AdminProvider({ children }: { children: React.ReactNode }) {
  const [isAdmin, setIsAdmin] = useState<boolean>(() => {
    if (FORCE_STATIC) return false
    return localStorage.getItem(STORAGE_KEY) === 'on'
  })

  useEffect(() => {
    if (FORCE_STATIC) return
    const params = new URLSearchParams(window.location.search)
    if (!params.has('admin')) return

    const value = params.get('admin')
    if (value === 'on') {
      localStorage.setItem(STORAGE_KEY, 'on')
      setIsAdmin(true)
    } else if (value === 'off') {
      localStorage.removeItem(STORAGE_KEY)
      setIsAdmin(false)
    }

    // Remove ?admin param from URL without reloading
    const url = new URL(window.location.href)
    url.searchParams.delete('admin')
    window.history.replaceState({}, '', url.toString())
  }, [])

  return (
    <AdminContext.Provider value={{ isAdmin }}>
      {children}
    </AdminContext.Provider>
  )
}

export function useAdmin(): AdminContextType {
  return useContext(AdminContext)
}
