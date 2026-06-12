import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './hooks/useAuth'
import { ToastProvider } from './components/Toast'
import ErrorBoundary from './components/ErrorBoundary'
import Layout from './components/Layout'
import LoginView from './views/LoginView'
import DashboardView from './views/DashboardView'
import KnowledgeView from './views/KnowledgeView'
import KnowledgeDetail from './views/KnowledgeDetail'
import ImportView from './views/ImportView'
import ChatView from './views/ChatView'
import WikiView from './views/WikiView'
import WikiDetail from './views/WikiDetail'
import GraphView from './views/GraphView'
import SettingsView from './views/SettingsView'

function ProtectedRoutes() {
  const { authenticated } = useAuth()
  if (!authenticated) return <Navigate to="/login" replace />
  return (
    <Layout />
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <ErrorBoundary>
            <Routes>
              <Route path="/login" element={<LoginView />} />
              <Route element={<ProtectedRoutes />}>
                <Route index element={<DashboardView />} />
                <Route path="knowledge" element={<KnowledgeView />} />
                <Route path="knowledge/:id" element={<KnowledgeDetail />} />
                <Route path="import" element={<ImportView />} />
                <Route path="chat" element={<ChatView />} />
                <Route path="wiki" element={<WikiView />} />
                <Route path="wiki/:id" element={<WikiDetail />} />
                <Route path="graph" element={<GraphView />} />
                <Route path="settings" element={<SettingsView />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Routes>
          </ErrorBoundary>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
