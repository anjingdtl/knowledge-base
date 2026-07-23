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
      <div className="w-full max-w-sm border border-[var(--color-border)] bg-[var(--color-surface)] rounded-xl p-7 shadow-[0_16px_48px_rgba(15,92,103,0.08)]">
        <div className="brand-lockup">
          <h1 className="brand-name"><span>ShineHe</span><span>Knowledge</span></h1>
          <p className="brand-tagline mt-2">安全访问本地知识库</p>
        </div>

        <form onSubmit={e => { e.preventDefault(); submit() }} className="mt-5 space-y-3">
          <input
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="用户名"
            aria-label="用户名"
            className="w-full px-3 py-2.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-input)] text-sm placeholder:text-[var(--color-text-muted)]"
          />
          <input
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="密码"
            aria-label="密码"
            type="password"
            className="w-full px-3 py-2.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-input)] text-sm placeholder:text-[var(--color-text-muted)]"
          />
          {error && <div className="text-xs text-[var(--color-danger)]">{error}</div>}
          <button
            type="submit"
            disabled={loading || !username.trim() || !password.trim()}
            className="w-full px-4 py-2.5 bg-[var(--color-primary)] text-white rounded-lg text-sm font-semibold hover:bg-[var(--color-primary-strong)] disabled:opacity-50"
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
