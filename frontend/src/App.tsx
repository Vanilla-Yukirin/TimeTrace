import { lazy, Suspense } from 'react'
import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Toaster } from 'sonner'
import { MainLayout } from './components/layout/AppShell'
import { RequireAuth } from './components/RequireAuth'
import { AuthProvider } from './contexts/AuthContext'
import { ThemeProvider } from './contexts/ThemeContext'
import { useTheme } from './contexts/theme'
import { SakuraPetals } from './components/brand/SakuraPetals'
import { Skeleton } from './components/ui/Skeleton'
import { UnauthorizedError } from './lib/api'
import { queryKeys } from './lib/queryKeys'
import { ChangePasswordPage } from './pages/ChangePasswordPage'
import { LoginPage } from './pages/LoginPage'
import { TimelinePage } from './pages/TimelinePage'

// Route-level code splitting: the timeline is the landing page and stays in
// the main bundle; everything else (Dashboard pulls react-markdown, AgentPage
// pulls the mermaid-capable Markdown renderer, …) loads on first visit.
const AgentPage = lazy(() => import('./pages/AgentPage').then((m) => ({ default: m.AgentPage })))
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage })))
const AuditPage = lazy(() => import('./pages/AuditPage').then((m) => ({ default: m.AuditPage })))
const LlmLogPage = lazy(() => import('./pages/LlmLogPage').then((m) => ({ default: m.LlmLogPage })))
const PyramidPage = lazy(() => import('./pages/PyramidPage').then((m) => ({ default: m.PyramidPage })))
const SearchPage = lazy(() => import('./pages/SearchPage').then((m) => ({ default: m.SearchPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))

function PageFallback() {
  return (
    <div style={{ flex: 1, padding: '24px 22px', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Skeleton style={{ height: 28, width: '40%' }} />
      <Skeleton style={{ height: 200 }} />
      <Skeleton style={{ height: 120 }} />
    </div>
  )
}

const queryClient = new QueryClient({
  // Global 401 recovery for the CURRENT tab: a 'storage' event never fires in
  // the tab that wrote it, so broadcastKick() (in apiFetch) only kicks OTHER
  // tabs. Any query that throws UnauthorizedError here clears the cached auth
  // principal, which flips AuthContext.user → null → RequireAuth redirects
  // THIS tab to /login. Without this, a mid-session 401 on a data query left
  // the tab stuck on a protected page until the next window-focus refetch.
  queryCache: new QueryCache({
    onError: (error) => {
      if (error instanceof UnauthorizedError) {
        queryClient.setQueryData(queryKeys.authMe(), null)
      }
    },
  }),
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

/** Toaster that follows the active theme (must live inside ThemeProvider). */
function ThemedToaster() {
  const { theme } = useTheme()
  return <Toaster position="top-center" richColors closeButton theme={theme} />
}

export function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          {/* AuthProvider must live inside the router so its 401 broadcast can
              coexist with route changes, and inside QueryClientProvider so
              useQuery works in it. */}
          <AuthProvider>
            <SakuraPetals />
            <ThemedToaster />
            <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/login/change-password"
              element={
                <RequireAuth allowMustChange>
                  <ChangePasswordPage />
                </RequireAuth>
              }
            />
            <Route
              path="/*"
              element={
                <RequireAuth>
                  <MainLayout>
                    <Suspense fallback={<PageFallback />}>
                      <Routes>
                        <Route path="/" element={<TimelinePage />} />
                        <Route path="/agent" element={<AgentPage />} />
                        <Route path="/dashboard" element={<DashboardPage />} />
                        <Route path="/audit" element={<AuditPage />} />
                        <Route path="/pyramid" element={<PyramidPage />} />
                        <Route path="/llm-log" element={<LlmLogPage />} />
                        <Route path="/search" element={<SearchPage />} />
                        <Route path="/settings" element={<SettingsPage />} />
                      </Routes>
                    </Suspense>
                  </MainLayout>
                </RequireAuth>
              }
            />
            </Routes>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  )
}

export default App
