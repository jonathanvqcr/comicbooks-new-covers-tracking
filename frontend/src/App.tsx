import React from 'react'
import { Routes, Route } from 'react-router-dom'
import { NotificationProvider } from './context/NotificationContext'
import { AdminProvider, useAdmin } from './context/AdminContext'
import Layout from './components/Layout/Layout'
import Dashboard from './pages/Dashboard/Dashboard'
import Notifications from './pages/Notifications/Notifications'
import Reports from './pages/Reports/Reports'
import Settings from './pages/Settings/Settings'

function AppRoutes() {
  const { isAdmin } = useAdmin()
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Dashboard />} />
        {isAdmin && <Route path="notifications" element={<Notifications />} />}
        {isAdmin && <Route path="reports" element={<Reports />} />}
        {isAdmin && <Route path="settings" element={<Settings />} />}
      </Route>
    </Routes>
  )
}

export default function App() {
  return (
    <AdminProvider>
      <NotificationProvider>
        <AppRoutes />
      </NotificationProvider>
    </AdminProvider>
  )
}
