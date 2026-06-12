import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet } from '../api'
import PageHeader from '../components/PageHeader'

interface Stats {
  knowledge_count: number
  block_count: number
  vector_count: number
  wiki_count: number
  conversation_count: number
  agent_memory_count: number
}

interface RecentJob {
  id: string
  type: string
  status: string
  created_at: string
  metadata?: Record<string, unknown>
}

export default function DashboardView() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [jobs, setJobs] = useState<RecentJob[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      apiGet<Stats>('/api/stats').catch(() => null),
      apiGet<{ jobs: RecentJob[] }>('/api/jobs?limit=5').catch(() => ({ jobs: [] })),
    ]).then(([s, j]) => {
      if (s) setStats(s)
      setJobs(j?.jobs || [])
      setLoading(false)
    })
  }, [])

  const statCards = [
    { label: '知识条目', value: stats?.knowledge_count ?? '-', to: '/knowledge' },
    { label: '内容块', value: stats?.block_count ?? '-', to: '/knowledge' },
    { label: '向量索引', value: stats?.vector_count ?? '-', to: '/knowledge' },
    { label: 'Wiki 页面', value: stats?.wiki_count ?? '-', to: '/wiki' },
    { label: '对话记录', value: stats?.conversation_count ?? '-', to: '/chat' },
    { label: 'Agent 记忆', value: stats?.agent_memory_count ?? '-', to: '/settings' },
  ]

  return (
    <div>
      <PageHeader title="仪表盘" subtitle="知识库全局概览" />

      {loading ? (
        <div className="text-[var(--color-text-muted)]">加载中...</div>
      ) : (
        <>
          {/* 统计卡片 */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
            {statCards.map(card => (
              <Link
                key={card.label}
                to={card.to}
                className="p-4 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)] hover:border-[var(--color-primary)] transition-colors"
              >
                <div className="text-2xl font-bold text-[var(--color-primary)]">{card.value}</div>
                <div className="mt-1 text-xs text-[var(--color-text-muted)]">{card.label}</div>
              </Link>
            ))}
          </div>

          {/* 最近任务 */}
          <div>
            <h3 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">最近导入任务</h3>
            {jobs.length === 0 ? (
              <div className="text-sm text-[var(--color-text-muted)] py-4 text-center bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
                暂无导入任务
              </div>
            ) : (
              <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg)]">
                      <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">类型</th>
                      <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">状态</th>
                      <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map(job => (
                      <tr key={job.id} className="border-b border-[var(--color-border)] last:border-b-0">
                        <td className="px-4 py-2">{job.type}</td>
                        <td className="px-4 py-2">
                          <span className={`text-xs px-2 py-0.5 rounded-full ${
                            job.status === 'completed' ? 'bg-green-100 text-green-700'
                            : job.status === 'failed' ? 'bg-red-100 text-red-600'
                            : job.status === 'running' ? 'bg-blue-100 text-blue-700'
                            : 'bg-yellow-100 text-yellow-700'
                          }`}>
                            {job.status}
                          </span>
                        </td>
                        <td className="px-4 py-2 text-[var(--color-text-muted)]">{job.created_at?.slice(0, 16)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* 快捷操作 */}
          <div className="mt-8 grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[
              { label: '导入文件', to: '/import', icon: '📄' },
              { label: '智能问答', to: '/chat', icon: '💬' },
              { label: 'Wiki 管理', to: '/wiki', icon: '📝' },
              { label: '图谱浏览', to: '/graph', icon: '🕸' },
            ].map(action => (
              <Link
                key={action.label}
                to={action.to}
                className="p-4 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)] text-center hover:border-[var(--color-primary)] transition-colors"
              >
                <div className="text-2xl mb-1">{action.icon}</div>
                <div className="text-sm font-medium">{action.label}</div>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
