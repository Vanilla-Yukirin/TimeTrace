import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { useIsMobile } from '@/hooks/useIsMobile'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {children}
    </div>
  )
}

/** The authed app shell: top bar + (desktop) static sidebar / (mobile) slide-in
 *  drawer + main content. The mobile drawer open-state lives here because the
 *  hamburger (in TopBar) and the drawer (Sidebar) must share it. */
export function MainLayout({ children }: { children: React.ReactNode }) {
  const isMobile = useIsMobile()
  const [navOpen, setNavOpen] = useState(false)

  // Leaving mobile (e.g. rotate / resize) must not leave a stuck-open drawer.
  useEffect(() => {
    if (!isMobile) setNavOpen(false)
  }, [isMobile])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <TopBar isMobile={isMobile} onMenu={() => setNavOpen(true)} />
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden', minHeight: 0 }}>
        <Sidebar isMobile={isMobile} open={navOpen} onClose={() => setNavOpen(false)} />
        <main
          style={{
            flex: 1,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            minWidth: 0,
          }}
        >
          {children}
        </main>
      </div>
    </div>
  )
}

export { Outlet }
