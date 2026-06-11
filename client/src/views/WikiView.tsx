import React, { useEffect, useState } from 'react'
import { apiGet, apiPost } from '../api'

/* ---------- types ---------- */
interface WikiPage {
  id: string
  title: string
  summary: string
  status: string
  lint_score: number | null
  tags: string
  created_at: string
  updated_at: string
}

interface LintIssue {
  page_id?: string
  title?: string
  rule?: string
  message?: string
  severity?: string
  [key: string]: unknown
}

interface LintResult {
  issues?: LintIssue[]
  summary?: Record<string, number>
  [key: string]: unknown
}

interface DeadLink {
  source_page_id: string
  source_title: string
  dead_ref: string
}

interface PreviewResult {
  status: string
  scanned: number
  total_dead_links: number
  dead_links: DeadLink[]
}

interface RepairResult {
  status: string
  scanned: number
  pages_with_dead_refs?: number
  fixed?: number
  redirects?: number
  stubs?: number
  removes?: number
  errors?: number
  details?: Array<Record<string, unknown>>
  [key: string]: unknown
}

type Tab = 'pages' | 'lint' | 'repair'

/* ---------- helpers ---------- */
function safeTags(raw: string): string[] {
  try { return JSON.parse(raw || '[]') } catch { return [] }
}

function statusLabel(s: string) {
  const map: Record<string, string> = {
    published: '已发布', draft: '草稿', deprecated: '已废弃', deleted: '回收站',
  }
  return map[s] || s
}

function statusColor(s: string) {
  const map: Record<string, string> = {
    published: 'bg-green-100 text-green-700',
    draft: 'bg-yellow-100 text-yellow-700',
    deprecated: 'bg-gray-100 text-gray-500',
    deleted: 'bg-red-100 text-red-600',
  }
  return map[s] || 'bg-gray-100 text-gray-500'
}

/* ---------- component ---------- */
export default function WikiView() {
  const [tab, setTab] = useState<Tab>('pages')

  return (
    <div>
      {/* header */}
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold">Wiki 知识管理</h2>
        <div className="flex gap-1 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-0.5">
          {([['pages', '页面列表'], ['lint', '质量检查'], ['repair', '死链修复']] as [Tab, string][]).map(([k, l]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`px-3 py-1 rounded-md text-sm transition-colors ${
                tab === k
                  ? 'bg-[var(--color-primary)] text-white'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-primary)]'
              }`}
            >
              {l}
            </button>
          ))}
        </div>
      </div>

      {tab === 'pages' && <PagesTab />}
      {tab === 'lint' && <LintTab />}
      {tab === 'repair' && <RepairTab />}
    </div>
  )
}

/* ===================== Pages Tab ===================== */
function PagesTab() {
  const [pages, setPages] = useState<WikiPage[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  const fetchPages = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: '50', offset: '0' })
      if (statusFilter) params.set('status', statusFilter)
      if (search.trim()) params.set('search', search.trim())
      const data = await apiGet<{ pages: WikiPage[]; total: number }>(`/api/wiki/pages?${params}`)
      setPages(data.pages || [])
      setTotal(data.total || 0)
    } catch (err) {
      console.error('Failed to fetch wiki pages:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchPages() }, [statusFilter])

  const handleSearch = () => fetchPages()

  return (
    <div>
      {/* toolbar */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          placeholder="搜索页面标题..."
          className="px-3 py-1.5 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm flex-1 min-w-[180px]"
        />
        <button onClick={handleSearch} className="px-4 py-1.5 bg-[var(--color-primary)] text-white rounded-lg text-sm hover:opacity-90">
          搜索
        </button>
        <select
          value={statusFilter || ''}
          onChange={e => setStatusFilter(e.target.value || null)}
          className="px-3 py-1.5 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm"
        >
          <option value="">全部状态</option>
          <option value="published">已发布</option>
          <option value="draft">草稿</option>
          <option value="deprecated">已废弃</option>
        </select>
        <span className="text-sm text-[var(--color-text-muted)]">共 {total} 页</span>
      </div>

      {/* page list */}
      {loading ? (
        <div className="text-[var(--color-text-muted)]">加载中...</div>
      ) : pages.length === 0 ? (
        <div className="text-[var(--color-text-muted)] py-10 text-center">暂无 Wiki 页面</div>
      ) : (
        <div className="grid gap-3">
          {pages.map(p => (
            <div key={p.id} className="p-4 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <h3 className="font-medium truncate">{p.title}</h3>
                  <p className="mt-1 text-sm text-[var(--color-text-muted)] line-clamp-2">
                    {p.summary || '暂无摘要'}
                  </p>
                </div>
                <span className={`shrink-0 text-xs px-2 py-0.5 rounded-full ${statusColor(p.status)}`}>
                  {statusLabel(p.status)}
                </span>
              </div>
              <div className="mt-2 flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
                {p.tags && safeTags(p.tags).length > 0 && (
                  <div className="flex gap-1 flex-wrap">
                    {safeTags(p.tags).map((t: string) => (
                      <span key={t} className="px-1.5 py-0.5 bg-[var(--color-primary)]/10 text-[var(--color-primary)] rounded text-[11px]">
                        {t}
                      </span>
                    ))}
                  </div>
                )}
                <span className="ml-auto shrink-0">{p.updated_at?.slice(0, 16)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ===================== Lint Tab ===================== */
function LintTab() {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<LintResult | null>(null)
  const [error, setError] = useState('')

  const runLint = async () => {
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const data = await apiPost<LintResult>('/api/wiki/lint', {})
      setResult(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : '检查失败')
    } finally {
      setLoading(false)
    }
  }

  const issues = result?.issues || []
  const summary = result?.summary || {}

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={runLint}
          disabled={loading}
          className="px-5 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
        >
          {loading ? '检查中...' : '运行质量检查'}
        </button>
        {result && (
          <span className="text-sm text-[var(--color-text-muted)]">
            发现 {issues.length} 个问题
          </span>
        )}
      </div>

      {error && (
        <div className="p-3 mb-4 bg-red-50 border border-[var(--color-danger)]/30 text-[var(--color-danger)] rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* summary stats */}
      {Object.keys(summary).length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          {Object.entries(summary).map(([rule, count]) => (
            <div key={rule} className="p-3 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)] text-center">
              <div className="text-2xl font-bold text-[var(--color-primary)]">{count as number}</div>
              <div className="text-xs text-[var(--color-text-muted)] mt-1">{rule}</div>
            </div>
          ))}
        </div>
      )}

      {/* issue list */}
      {issues.length > 0 && (
        <div className="grid gap-2">
          {issues.map((issue, i) => (
            <div key={i} className="p-3 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)] text-sm">
              <div className="flex items-start justify-between gap-2">
                <span className="font-medium">{issue.title || issue.page_id || `#${i + 1}`}</span>
                <span className={`shrink-0 text-xs px-2 py-0.5 rounded-full ${
                  issue.severity === 'error' ? 'bg-red-100 text-red-600'
                  : issue.severity === 'warning' ? 'bg-yellow-100 text-yellow-700'
                  : 'bg-gray-100 text-gray-500'
                }`}>
                  {issue.rule || issue.severity || 'info'}
                </span>
              </div>
              {issue.message && (
                <p className="mt-1 text-[var(--color-text-muted)]">{issue.message}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {result && issues.length === 0 && !error && (
        <div className="py-10 text-center text-[var(--color-text-muted)]">
          <div className="text-3xl mb-2">&#10003;</div>
          所有检查通过，Wiki 内容质量良好
        </div>
      )}
    </div>
  )
}

/* ===================== Repair Tab ===================== */
function RepairTab() {
  const [loading, setLoading] = useState(false)
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [repairResult, setRepairResult] = useState<RepairResult | null>(null)
  const [error, setError] = useState('')
  const [maxPages, setMaxPages] = useState(50)

  /* step 1: preview (dry_run) */
  const runPreview = async () => {
    setLoading(true)
    setError('')
    setPreview(null)
    setRepairResult(null)
    try {
      const data = await apiPost<PreviewResult>('/api/wiki/fix-dead-links', {
        max_pages: maxPages,
        dry_run: true,
      })
      setPreview(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : '扫描失败')
    } finally {
      setLoading(false)
    }
  }

  /* step 2: execute repair */
  const runRepair = async () => {
    setLoading(true)
    setError('')
    setRepairResult(null)
    try {
      const data = await apiPost<RepairResult>('/api/wiki/fix-dead-links', {
        max_pages: maxPages,
        dry_run: false,
      })
      setRepairResult(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : '修复失败')
    } finally {
      setLoading(false)
    }
  }

  const deadLinks = preview?.dead_links || []

  return (
    <div>
      <p className="text-sm text-[var(--color-text-muted)] mb-4">
        使用内置 LLM 智能分析 Wiki 页面中的 <code className="px-1 py-0.5 bg-[var(--color-surface)] rounded text-xs">[[死链]]</code>，
        自动选择修复策略：重定向到已有页面、创建占位页面或移除标记。
      </p>

      {/* controls */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <label className="text-sm text-[var(--color-text-muted)]">最大处理页数：</label>
        <input
          type="number"
          min={1}
          max={200}
          value={maxPages}
          onChange={e => setMaxPages(Number(e.target.value) || 50)}
          className="w-20 px-2 py-1.5 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm text-center"
        />
        <button
          onClick={runPreview}
          disabled={loading}
          className="px-4 py-2 bg-[var(--color-surface)] border border-[var(--color-primary)] text-[var(--color-primary)] rounded-lg text-sm hover:bg-[var(--color-primary-soft)] disabled:opacity-50"
        >
          {loading ? '扫描中...' : '扫描死链（预览）'}
        </button>
        {preview && preview.total_dead_links > 0 && (
          <button
            onClick={runRepair}
            disabled={loading}
            className="px-5 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
          >
            {loading ? '修复中...' : '执行 LLM 修复'}
          </button>
        )}
      </div>

      {error && (
        <div className="p-3 mb-4 bg-red-50 border border-[var(--color-danger)]/30 text-[var(--color-danger)] rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* preview results */}
      {preview && (
        <div className="mb-4">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-sm font-medium">
              扫描结果：{preview.scanned} 个页面，共 {preview.total_dead_links} 个死链
            </span>
          </div>

          {deadLinks.length === 0 ? (
            <div className="py-8 text-center text-[var(--color-text-muted)]">
              <div className="text-3xl mb-2">&#10003;</div>
              未发现死链，所有引用均指向已存在的页面
            </div>
          ) : (
            <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg)]">
                    <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">来源页面</th>
                    <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">死链引用</th>
                  </tr>
                </thead>
                <tbody>
                  {deadLinks.map((dl, i) => (
                    <tr key={i} className="border-b border-[var(--color-border)] last:border-b-0">
                      <td className="px-4 py-2">{dl.source_title}</td>
                      <td className="px-4 py-2">
                        <span className="px-2 py-0.5 bg-red-50 text-[var(--color-danger)] rounded text-xs font-mono">
                          [[{dl.dead_ref}]]
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* repair results */}
      {repairResult && (
        <div className="mt-4">
          <h3 className="text-sm font-medium mb-3">修复结果</h3>

          {/* stats cards */}
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-4">
            {[
              ['扫描页面', repairResult.scanned],
              ['重定向', repairResult.redirects ?? 0],
              ['创建占位', repairResult.stubs ?? 0],
              ['移除标记', repairResult.removes ?? 0],
              ['失败', repairResult.errors ?? 0],
            ].map(([label, val]) => (
              <div key={label as string} className="p-3 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)] text-center">
                <div className="text-2xl font-bold text-[var(--color-primary)]">{val as number}</div>
                <div className="text-xs text-[var(--color-text-muted)] mt-1">{label}</div>
              </div>
            ))}
          </div>

          <div className={`p-3 rounded-lg text-sm ${
            repairResult.status === 'clean'
              ? 'bg-green-50 text-green-700'
              : 'bg-[var(--color-surface)] border border-[var(--color-border)]'
          }`}>
            {repairResult.status === 'clean'
              ? 'Wiki 中没有死链，所有内容引用均有效。'
              : `修复完成。共处理 ${repairResult.fixed ?? 0} 处死链。`
            }
          </div>
        </div>
      )}
    </div>
  )
}
