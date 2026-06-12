import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

const NAV_ITEMS = [
  { to: '/', label: '仪表盘', icon: '◉' },
  { to: '/knowledge', label: '知识库', icon: 'KB' },
  { to: '/import', label: '导入中心', icon: '↑' },
  { to: '/chat', label: '智能问答', icon: 'AI' },
  { to: '/wiki', label: 'Wiki', icon: 'WK' },
  { to: '/graph', label: '知识图谱', icon: 'GR' },
  { to: '/settings', label: '设置', icon: '⚙' },
]

export default function Layout() {
  const { logout } = useAuth()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <div className="flex h-screen bg-[var(--color-bg)] text-[var(--color-text)]">
      <nav className="w-56 bg-[var(--color-sidebar)] border-r border-[var(--color-border)] flex flex-col p-4 gap-2 shrink-0">
        <div className="px-2 py-3">
          <NavLink to="/" className="block">
            <h1 className="text-xl font-bold text-[var(--color-primary)]">ShineHeKnowledge</h1>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">ShineHe Knowledge Engine</p>
          </NavLink>
          <div className="mt-3 h-0.5 rounded bg-gradient-to-r from-[var(--color-primary)] to-[var(--color-accent)]" />
        </div>

        <div className="mt-2 flex flex-col gap-1">
          {NAV_ITEMS.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  isActive
                    ? 'bg-[var(--color-primary-soft)] text-[var(--color-primary)] border-l-4 border-[var(--color-primary)]'
                    : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)] border-l-4 border-transparent'
                }`
              }
            >
              <span className="w-7 text-xs font-semibold">{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </div>

        <div className="mt-auto pt-4 border-t border-[var(--color-border)]">
          <button
            onClick={handleLogout}
            className="w-full px-3 py-2 rounded-lg text-sm text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          >
            退出登录
          </button>
        </div>
      </nav>

      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  )
}
