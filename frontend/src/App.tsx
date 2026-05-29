import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Toaster } from 'sonner'
import { MainLayout } from './components/layout/AppShell'
import { RequireAuth } from './components/RequireAuth'
import { AuthProvider } from './contexts/AuthContext'
import { Sidebar } from './components/layout/Sidebar'
import { TopBar } from './components/layout/TopBar'
import { ChangePasswordPage } from './pages/ChangePasswordPage'
import { LoginPage } from './pages/LoginPage'
import { TimelinePage } from './pages/TimelinePage'
import { SearchPage } from './pages/SearchPage'
import { SettingsPage } from './pages/SettingsPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/* AuthProvider must live inside the router so its 401 broadcast can
            coexist with route changes, and inside QueryClientProvider so
            useQuery works in it. */}
        <AuthProvider>
          <Toaster position="top-center" richColors closeButton theme="dark" />
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
                  <MainLayout sidebar={<Sidebar />} topbar={<TopBar />}>
                    <Routes>
                      <Route path="/" element={<TimelinePage />} />
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
  )
}

export default App
