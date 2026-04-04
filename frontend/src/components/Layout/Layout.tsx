import React, { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useNotifications } from '../../context/NotificationContext'
import { useAdmin } from '../../context/AdminContext'
import NotificationPanel from '../NotificationPanel/NotificationPanel'
import styles from './Layout.module.css'

export default function Layout() {
  const { unreadCount } = useNotifications()
  const { isAdmin } = useAdmin()
  const [panelOpen, setPanelOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div className={styles.root}>
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <span className={styles.logo}>🗓 Comic Tracker</span>
          <nav className={styles.nav}>
            <NavLink to="/" end className={({ isActive }) => isActive ? styles.active : ''}>Dashboard</NavLink>
            <NavLink to="/notifications" className={({ isActive }) => isActive ? styles.active : ''}>Notifications</NavLink>
            {isAdmin && <NavLink to="/reports" className={({ isActive }) => isActive ? styles.active : ''}>Reports</NavLink>}
            {isAdmin && <NavLink to="/settings" className={({ isActive }) => isActive ? styles.active : ''}>Settings</NavLink>}
          </nav>
          <div className={styles.headerRight}>
            <button
              className={styles.bell}
              onClick={() => setPanelOpen(o => !o)}
              aria-label="Notifications"
            >
              🔔
              {unreadCount > 0 && (
                <span className={styles.badge}>{unreadCount > 99 ? '99+' : unreadCount}</span>
              )}
            </button>
            <button
              className={styles.menuBtn}
              onClick={() => setMenuOpen(o => !o)}
              aria-label="Menu"
            >
              {menuOpen ? '✕' : '☰'}
            </button>
          </div>
        </div>

        {menuOpen && (
          <nav className={styles.navDrawer}>
            <NavLink to="/" end className={({ isActive }) => isActive ? styles.drawerActive : styles.drawerLink} onClick={() => setMenuOpen(false)}>Dashboard</NavLink>
            <NavLink to="/notifications" className={({ isActive }) => isActive ? styles.drawerActive : styles.drawerLink} onClick={() => setMenuOpen(false)}>Notifications</NavLink>
            {isAdmin && <NavLink to="/reports" className={({ isActive }) => isActive ? styles.drawerActive : styles.drawerLink} onClick={() => setMenuOpen(false)}>Reports</NavLink>}
            {isAdmin && <NavLink to="/settings" className={({ isActive }) => isActive ? styles.drawerActive : styles.drawerLink} onClick={() => setMenuOpen(false)}>Settings</NavLink>}
          </nav>
        )}
      </header>

      <main className={styles.main}>
        <Outlet />
      </main>

      {panelOpen && <NotificationPanel onClose={() => setPanelOpen(false)} />}
    </div>
  )
}
