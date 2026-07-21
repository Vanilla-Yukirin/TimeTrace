import { useState } from 'react'
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

  // Changing breakpoint remounts the stateful shell, so a mobile drawer cannot
  // remain open after rotating to desktop and back.
  return (
    <MainLayoutContents key={isMobile ? 'mobile' : 'desktop'} isMobile={isMobile}>
      {children}
    </MainLayoutContents>
  )
}

function MainLayoutContents({
  children,
  isMobile,
}: {
  children: React.ReactNode
  isMobile: boolean
}) {
  const [navOpen, setNavOpen] = useState(false)

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
