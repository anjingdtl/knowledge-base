import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

export default function LoginView() {
  const { login } = useAuth()
  const navigate = useNavigate()
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
      login(data.access_token)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : '认证失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[var(--color-bg)] text-[var(--color-text)] flex items-center justify-center px-4">
      <div className="w-full max-w-sm border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg p-6 shadow-sm">
        <h1 className="text-xl font-bold text-[var(--color-primary)]">ShineHeKnowledge</h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">登录后访问本地知识库 API</p>

        <form onSubmit={e => { e.preventDefault(); submit() }} className="mt-5 space-y-3">
          <input
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="用户名"
            aria-label="用户名"
            className="w-full px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-input)] text-sm"
          />
          <input
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="密码"
            aria-label="密码"
            type="password"
            className="w-full px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-input)] text-sm"
          />
          {error && <div className="text-xs text-[var(--color-danger)]">{error}</div>}
          <button
            type="submit"
            disabled={loading || !username.trim() || !password.trim()}
            className="w-full px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm disabled:opacity-50"
          >
            {loading ? '处理中...' : mode === 'login' ? '登录' : '注册首个用户'}
          </button>
          <button
            type="button"
            onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
            className="w-full text-xs text-[var(--color-primary)]"
          >
            {mode === 'login' ? '首次使用？注册管理员' : '已有账号？返回登录'}
          </button>
        </form>
      </div>
    </div>
  )
}
