import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Toaster } from 'sonner'
import { MainLayout } from './components/layout/AppShell'
import { RequireAuth } from './components/RequireAuth'
import { AuthProvider } from './contexts/AuthContext'
import { ThemeProvider } from './contexts/ThemeContext'
import { useTheme } from './contexts/theme'
import { SakuraPetals } from './components/brand/SakuraPetals'
import { UnauthorizedError } from './lib/api'
import { queryKeys } from './lib/queryKeys'
import { ChangePasswordPage } from './pages/ChangePasswordPage'
import { LoginPage } from './pages/LoginPage'
import { TimelinePage } from './pages/TimelinePage'
import { AgentPage } from './pages/AgentPage'
import { DashboardPage } from './pages/DashboardPage'
import { AuditPage } from './pages/AuditPage'
import { LlmLogPage } from './pages/LlmLogPage'
import { PyramidPage } from './pages/PyramidPage'
import { SearchPage } from './pages/SearchPage'
import { SettingsPage } from './pages/SettingsPage'

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
