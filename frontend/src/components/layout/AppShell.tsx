import { Outlet } from 'react-router-dom'

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {children}
    </div>
  )
}

export function MainLayout({ sidebar, topbar, children }: {
  sidebar: React.ReactNode
  topbar: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {topbar}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {sidebar}
        <main style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {children}
        </main>
      </div>
    </div>
  )
}

export { Outlet }
