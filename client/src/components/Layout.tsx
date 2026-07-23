import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

const NAV_ITEMS = [
  { to: '/', label: '仪表盘', icon: '◉' },
  { to: '/knowledge', label: '知识库', icon: 'KB' },
  { to: '/maintenance', label: '维护中心', icon: 'MT' },
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
    <div className="flex h-screen min-w-0 bg-[var(--color-bg)] text-[var(--color-text)]">
      <nav className="app-sidebar w-72 min-w-64 bg-[var(--color-sidebar)] border-r border-[var(--color-border)] flex flex-col px-4 py-5 gap-2 shrink-0">
        <div className="px-3 py-2">
          <NavLink to="/" className="block">
            <div className="brand-lockup">
              <h1 className="brand-name"><span>ShineHe</span><span>Knowledge</span></h1>
              <p className="brand-tagline mt-2">本地优先 · 知识检索引擎</p>
            </div>
          </NavLink>
          <div className="mt-4 h-px rounded bg-gradient-to-r from-[var(--color-primary)] via-[var(--color-accent)] to-transparent" />
        </div>

        <div className="mt-3 flex flex-col gap-1">
          {NAV_ITEMS.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `flex min-w-0 items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-[var(--color-primary-soft)] text-[var(--color-primary)] shadow-[inset_3px_0_0_var(--color-primary)]'
                    : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)]'
                }`
              }
            >
              <span className="nav-item-icon" aria-hidden="true">{item.icon}</span>
              <span className="nav-item-label">{item.label}</span>
            </NavLink>
          ))}
        </div>

        <div className="mt-auto px-1 pt-5 border-t border-[var(--color-border)]">
          <button
            onClick={handleLogout}
            className="w-full px-3 py-2.5 rounded-lg text-left text-sm font-medium text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)]"
          >
            退出登录
          </button>
        </div>
      </nav>

      <main className="min-w-0 flex-1 overflow-auto p-6 lg:p-8">
        <Outlet />
      </main>
    </div>
  )
}
