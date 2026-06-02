import { useState } from 'react'
import KnowledgeView from './views/KnowledgeView'
import ChatView from './views/ChatView'
import WikiView from './views/WikiView'
import GraphView from './views/GraphView'
import SettingsView from './views/SettingsView'
import { clearToken, getToken, setToken } from './api'

type Tab = 'knowledge' | 'chat' | 'wiki' | 'graph' | 'settings'

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: 'knowledge', label: '知识库', icon: 'KB' },
  { key: 'chat', label: '智能问答', icon: 'AI' },
  { key: 'wiki', label: 'Wiki', icon: 'WK' },
  { key: 'graph', label: '知识图谱', icon: 'GR' },
  { key: 'settings', label: '设置', icon: 'ST' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('knowledge')
  const [authed, setAuthed] = useState(Boolean(getToken()))

  if (!authed) {
    return <LoginView onLogin={() => setAuthed(true)} />
  }

  const renderView = () => {
    switch (activeTab) {
      case 'knowledge': return <KnowledgeView />
      case 'chat': return <ChatView />
      case 'wiki': return <WikiView />
      case 'graph': return <GraphView />
      case 'settings': return <SettingsView />
    }
  }

  const logout = () => {
    clearToken()
    setAuthed(false)
  }

  return (
    <div className="flex h-screen bg-[var(--color-bg)] text-[var(--color-text)]">
      <nav className="w-56 bg-[var(--color-sidebar)] border-r border-[var(--color-border)] flex flex-col p-4 gap-2">
        <div className="px-2 py-3">
          <h1 className="text-xl font-bold text-[var(--color-primary)]">泰坦知识库</h1>
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">ShineHe Knowledge Engine</p>
          <div className="mt-3 h-0.5 rounded bg-gradient-to-r from-[var(--color-primary)] to-[var(--color-accent)]" />
        </div>

        <div className="mt-2 flex flex-col gap-1">
          {TABS.map(tab => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors
                ${activeTab === tab.key
                  ? 'bg-[var(--color-primary-soft)] text-[var(--color-primary)] border-l-4 border-[var(--color-primary)]'
                  : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)] border-l-4 border-transparent'
                }`}
            >
              <span className="w-7 text-xs font-semibold">{tab.icon}</span>
              <span>{tab.label}</span>
            </button>
          ))}
        </div>

        <div className="mt-auto pt-4 border-t border-[var(--color-border)]">
          <button
            onClick={logout}
            className="w-full px-3 py-2 rounded-lg text-sm text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          >
            退出登录
          </button>
        </div>
      </nav>

      <main className="flex-1 overflow-auto p-6">
        {renderView()}
      </main>
    </div>
  )
}

function LoginView({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!username.trim() || !password.trim() || loading) return
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`/api/auth/${mode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      })
      if (!res.ok) {
        throw new Error(await res.text())
      }
      const data = await res.json()
      setToken(data.access_token)
      onLogin()
    } catch (err) {
      setError(err instanceof Error ? err.message : '认证失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[var(--color-bg)] text-[var(--color-text)] flex items-center justify-center px-4">
      <div className="w-full max-w-sm border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg p-6 shadow-sm">
        <h1 className="text-xl font-bold text-[var(--color-primary)]">泰坦知识库</h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">登录后访问本地知识库 API</p>

        <div className="mt-5 space-y-3">
          <input
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="用户名"
            className="w-full px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-input)] text-sm"
          />
          <input
            value={password}
            onChange={e => setPassword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && submit()}
            placeholder="密码"
            type="password"
            className="w-full px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-input)] text-sm"
          />
          {error && <div className="text-xs text-[var(--color-danger)]">{error}</div>}
          <button
            onClick={submit}
            disabled={loading || !username.trim() || !password.trim()}
            className="w-full px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm disabled:opacity-50"
          >
            {loading ? '处理中...' : mode === 'login' ? '登录' : '注册首个用户'}
          </button>
          <button
            onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
            className="w-full text-xs text-[var(--color-primary)]"
          >
            {mode === 'login' ? '首次使用？注册管理员' : '已有账号？返回登录'}
          </button>
        </div>
      </div>
    </div>
  )
}
