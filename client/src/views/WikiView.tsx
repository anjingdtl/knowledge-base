import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet, apiPost } from '../api'
import { useToast } from '../components/Toast'
import PageHeader from '../components/PageHeader'
import { safeTags } from '../utils/helpers'

/* ---------- types ---------- */
interface WikiPage {
  id: string
  title: string
  summary: string
  status: string
  lint_score: number | null
  complex_anomaly?: string
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

type Tab = 'pages' | 'lint' | 'repair' | 'complex'

/* ---------- helpers ---------- */

function statusLabel(s: string) {
  const map: Record<string, string> = {
    published: '已发布', draft: '草稿', review: '审核中', deprecated: '已废弃', deleted: '回收站',
  }
  return map[s] || s
}

function statusColor(s: string) {
  const map: Record<string, string> = {
    published: 'bg-green-100 text-green-700',
    draft: 'bg-yellow-100 text-yellow-700',
    review: 'bg-blue-100 text-blue-700',
    deprecated: 'bg-gray-100 text-gray-500',
    deleted: 'bg-red-100 text-red-600',
  }
  return map[s] || 'bg-gray-100 text-gray-500'
}

/* ---------- component ---------- */
export default function WikiView() {
  const [tab, setTab] = useState<Tab>('pages')
  const navigate = useNavigate()

  return (
    <div>
      <PageHeader
        title="Wiki 知识管理"
        actions={
          <div className="flex gap-1 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-0.5">
            {([['pages', '页面列表'], ['lint', '质量检查'], ['repair', '死链修复'], ['complex', '复杂修复']] as [Tab, string][]).map(([k, l]) => (
              <button
                key={k}
                onClick={() => setTab(k)}
                className={`px-3 py-1 rounded-md text-sm transition-colors ${
                  tab === k ? 'bg-[var(--color-primary)] text-white' : 'text-[var(--color-text-muted)] hover:text-[var(--color-primary)]'
                }`}
              >
                {l}
              </button>
            ))}
          </div>
        }
      />

      {tab === 'pages' && <PagesTab onNavigate={id => navigate(`/wiki/${id}`)} />}
      {tab === 'lint' && <LintTab />}
      {tab === 'repair' && <RepairTab />}
      {tab === 'complex' && <ComplexTab />}
    </div>
  )
}

/* ===================== Pages Tab ===================== */
function PagesTab({ onNavigate }: { onNavigate: (id: string) => void }) {
  const { toast } = useToast()
  const [pages, setPages] = useState<WikiPage[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)
  const [newTitle, setNewTitle] = useState('')

  const fetchPages = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: '50', offset: '0' })
      if (statusFilter) params.set('status', statusFilter)
      if (search.trim()) params.set('search', search.trim())
      const data = await apiGet<{ pages: WikiPage[]; total: number }>(`/api/wiki/pages?${params}`)
      setPages(data.pages || [])
      setTotal(data.total || 0)
    } catch {
      toast('加载 Wiki 页面失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchPages() }, [statusFilter])

  const handleCreate = async () => {
    if (!newTitle.trim()) return
    try {
      const data = await apiPost<{ id: string }>('/api/wiki/pages', { title: newTitle.trim(), content: '' })
      toast('页面已创建', 'success')
      setCreating(false)
      setNewTitle('')
      onNavigate(data.id)
    } catch (err) {
      toast(err instanceof Error ? err.message : '创建失败', 'error')
    }
  }

  return (
    <div>
      {/* toolbar */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && fetchPages()}
          placeholder="搜索页面标题..."
          className="px-3 py-1.5 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm flex-1 min-w-[180px]"
        />
        <button onClick={fetchPages} className="px-4 py-1.5 bg-[var(--color-primary)] text-white rounded-lg text-sm hover:opacity-90">
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
          <option value="review">审核中</option>
          <option value="deprecated">已废弃</option>
        </select>
        <button
          onClick={() => setCreating(true)}
          className="px-4 py-1.5 border border-[var(--color-primary)] text-[var(--color-primary)] rounded-lg text-sm hover:bg-[var(--color-primary-soft)]"
        >
          + 新建页面
        </button>
        <span className="text-sm text-[var(--color-text-muted)]">共 {total} 页</span>
      </div>

      {/* 新建表单 */}
      {creating && (
        <div className="flex items-center gap-2 mb-4 p-3 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg">
          <input
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleCreate()}
            placeholder="输入页面标题..."
            className="flex-1 px-3 py-1.5 border border-[var(--color-border)] rounded-lg text-sm bg-[var(--color-input)]"
            autoFocus
          />
          <button onClick={handleCreate} className="px-4 py-1.5 bg-[var(--color-primary)] text-white rounded-lg text-sm">创建</button>
          <button onClick={() => { setCreating(false); setNewTitle('') }} className="px-4 py-1.5 border border-[var(--color-border)] rounded-lg text-sm">取消</button>
        </div>
      )}

      {/* page list */}
      {loading ? (
        <div className="text-[var(--color-text-muted)]">加载中...</div>
      ) : pages.length === 0 ? (
        <div className="text-[var(--color-text-muted)] py-10 text-center">暂无 Wiki 页面</div>
      ) : (
        <div className="grid gap-3">
          {pages.map(p => (
            <div
              key={p.id}
              onClick={() => onNavigate(p.id)}
              className="p-4 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)] cursor-pointer hover:border-[var(--color-primary)] transition-colors"
            >
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
                {p.complex_anomaly && (
                  <span className="shrink-0 text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-700">
                    复杂异常
                  </span>
                )}
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
  const { toast } = useToast()
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
      toast('质量检查完成', 'success')
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
          <span className="text-sm text-[var(--color-text-muted)]">发现 {issues.length} 个问题</span>
        )}
      </div>

      {error && (
        <div className="p-3 mb-4 bg-red-50 border border-[var(--color-danger)]/30 text-[var(--color-danger)] rounded-lg text-sm">{error}</div>
      )}

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
              {issue.message && <p className="mt-1 text-[var(--color-text-muted)]">{issue.message}</p>}
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
  const { toast } = useToast()
  const [loading, setLoading] = useState(false)
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [repairResult, setRepairResult] = useState<RepairResult | null>(null)
  const [error, setError] = useState('')
  const [maxPages, setMaxPages] = useState(50)

  const runPreview = async () => {
    setLoading(true); setError(''); setPreview(null); setRepairResult(null)
    try {
      const data = await apiPost<PreviewResult>('/api/wiki/fix-dead-links', { max_pages: maxPages, dry_run: true })
      setPreview(data)
    } catch (err) { setError(err instanceof Error ? err.message : '扫描失败') }
    finally { setLoading(false) }
  }

  const runRepair = async () => {
    setLoading(true); setError(''); setRepairResult(null)
    try {
      const data = await apiPost<RepairResult>('/api/wiki/fix-dead-links', { max_pages: maxPages, dry_run: false })
      setRepairResult(data)
      toast('修复完成', 'success')
    } catch (err) { setError(err instanceof Error ? err.message : '修复失败') }
    finally { setLoading(false) }
  }

  const deadLinks = preview?.dead_links || []

  return (
    <div>
      <p className="text-sm text-[var(--color-text-muted)] mb-4">
        使用内置 LLM 智能分析 Wiki 页面中的 <code className="px-1 py-0.5 bg-[var(--color-surface)] rounded text-xs">[[死链]]</code>，
        自动选择修复策略：重定向到已有页面、创建占位页面或移除标记。
      </p>

      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <label className="text-sm text-[var(--color-text-muted)]">最大处理页数：</label>
        <input
          type="number" min={1} max={200} value={maxPages}
          onChange={e => setMaxPages(Number(e.target.value) || 50)}
          className="w-20 px-2 py-1.5 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm text-center"
        />
        <button onClick={runPreview} disabled={loading}
          className="px-4 py-2 bg-[var(--color-surface)] border border-[var(--color-primary)] text-[var(--color-primary)] rounded-lg text-sm hover:bg-[var(--color-primary-soft)] disabled:opacity-50">
          {loading ? '扫描中...' : '扫描死链（预览）'}
        </button>
        {preview && preview.total_dead_links > 0 && (
          <button onClick={runRepair} disabled={loading}
            className="px-5 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm hover:opacity-90 disabled:opacity-50">
            {loading ? '修复中...' : '执行 LLM 修复'}
          </button>
        )}
      </div>

      {error && <div className="p-3 mb-4 bg-red-50 border border-[var(--color-danger)]/30 text-[var(--color-danger)] rounded-lg text-sm">{error}</div>}

      {preview && (
        <div className="mb-4">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-sm font-medium">扫描结果：{preview.scanned} 个页面，共 {preview.total_dead_links} 个死链</span>
          </div>
          {deadLinks.length === 0 ? (
            <div className="py-8 text-center text-[var(--color-text-muted)]">
              <div className="text-3xl mb-2">&#10003;</div>未发现死链
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
                        <span className="px-2 py-0.5 bg-red-50 text-[var(--color-danger)] rounded text-xs font-mono">[[{dl.dead_ref}]]</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {repairResult && (
        <div className="mt-4">
          <h3 className="text-sm font-medium mb-3">修复结果</h3>
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
            repairResult.status === 'clean' ? 'bg-green-50 text-green-700' : 'bg-[var(--color-surface)] border border-[var(--color-border)]'
          }`}>
            {repairResult.status === 'clean' ? 'Wiki 中没有死链' : `修复完成。共处理 ${repairResult.fixed ?? 0} 处死链。`}
          </div>
        </div>
      )}
    </div>
  )
}

/* ===================== Complex Repair Tab ===================== */

interface ComplexIssue {
  page_id: string
  page_title: string
  categories: string[]
  duplicate_count?: number
  duplicate_ids?: string[]
}

interface ComplexScanResult {
  scanned: number
  total_issues: number
  issues: ComplexIssue[]
  pre_marked: Array<{ page_id: string; page_title: string; anomaly_types: string }>
}

interface ComplexRepairResult {
  status: string
  orphan_fixed?: number
  empty_fixed?: number
  duplicate_fixed?: number
  errors?: number
  details?: Array<Record<string, unknown>>
}

function ComplexTab() {
  const { toast } = useToast()
  const [loading, setLoading] = useState(false)
  const [scanResult, setScanResult] = useState<ComplexScanResult | null>(null)
  const [repairResult, setRepairResult] = useState<ComplexRepairResult | null>(null)
  const [error, setError] = useState('')
  const [confirmDialog, setConfirmDialog] = useState<'repair' | 'mark' | null>(null)

  const runScan = async () => {
    setLoading(true); setError(''); setScanResult(null); setRepairResult(null)
    try {
      const data = await apiPost<ComplexScanResult>('/api/wiki/complex-repair', { action: 'scan' })
      setScanResult(data)
      if (data.total_issues === 0 && data.pre_marked.length === 0) {
        toast('未发现复杂问题', 'success')
      }
    } catch (err) { setError(err instanceof Error ? err.message : '扫描失败') }
    finally { setLoading(false) }
  }

  const doRepair = async () => {
    setLoading(true); setError(''); setConfirmDialog(null)
    try {
      const issues = scanResult?.issues || []
      const data = await apiPost<ComplexRepairResult>('/api/wiki/complex-repair', {
        action: 'repair', issues,
      })
      setRepairResult(data)
      toast('修复完成', 'success')
      // 重新扫描获取最新状态
      const newScan = await apiPost<ComplexScanResult>('/api/wiki/complex-repair', { action: 'scan' })
      setScanResult(newScan)
    } catch (err) { setError(err instanceof Error ? err.message : '修复失败') }
    finally { setLoading(false) }
  }

  const doMark = async () => {
    setLoading(true); setError(''); setConfirmDialog(null)
    try {
      const issues = scanResult?.issues || []
      await apiPost('/api/wiki/complex-repair', { action: 'mark', issues })
      toast('已标记为复杂异常（未修复）', 'success')
      // 重新扫描
      const newScan = await apiPost<ComplexScanResult>('/api/wiki/complex-repair', { action: 'scan' })
      setScanResult(newScan)
    } catch (err) { setError(err instanceof Error ? err.message : '标记失败') }
    finally { setLoading(false) }
  }

  const categoryLabel: Record<string, string> = {
    orphan: '孤立页面', empty: '内容空洞', duplicate: '同名重复', contradiction: '内容矛盾',
  }

  const issues = scanResult?.issues || []
  const preMarked = scanResult?.pre_marked || []
  const hasPreMarked = preMarked.length > 0

  return (
    <div>
      <p className="text-sm text-[var(--color-text-muted)] mb-4">
        检测并修复复杂问题：孤立页面（无交叉引用）、内容空洞（摘要/正文为空）、同名重复、内容矛盾。
        扫描后可选择<b>立即修复</b>或<b>仅标记</b>。
      </p>

      {/* 已标记待修复提示 */}
      {hasPreMarked && !scanResult?.total_issues && (
        <div className="p-3 mb-4 bg-orange-50 border border-orange-300/50 text-orange-700 rounded-lg text-sm">
          存在 {preMarked.length} 个已标记「复杂异常」的页面待修复。点击下方按钮重新扫描。
        </div>
      )}

      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <button onClick={runScan} disabled={loading}
          className="px-5 py-2 bg-[var(--color-surface)] border border-[var(--color-primary)] text-[var(--color-primary)] rounded-lg text-sm hover:bg-[var(--color-primary-soft)] disabled:opacity-50">
          {loading ? '扫描中...' : '扫描复杂问题'}
        </button>
      </div>

      {error && <div className="p-3 mb-4 bg-red-50 border border-[var(--color-danger)]/30 text-[var(--color-danger)] rounded-lg text-sm">{error}</div>}

      {/* 扫描结果 + 确认对话框 */}
      {scanResult && (
        <div className="mb-4">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-sm font-medium">
              扫描 {scanResult.scanned} 个页面，发现 {scanResult.total_issues} 个复杂问题
              {preMarked.length > 0 && `，${preMarked.length} 个已标记待修复`}
            </span>
          </div>

          {issues.length === 0 && preMarked.length === 0 ? (
            <div className="py-8 text-center text-[var(--color-text-muted)]">
              <div className="text-3xl mb-2">&#10003;</div>未发现复杂问题
            </div>
          ) : (
            <>
              {/* 问题列表 */}
              <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg overflow-hidden mb-4">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg)]">
                      <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">页面</th>
                      <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">问题类型</th>
                      <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {issues.map((issue, i) => (
                      <tr key={i} className="border-b border-[var(--color-border)] last:border-b-0">
                        <td className="px-4 py-2">{issue.page_title}</td>
                        <td className="px-4 py-2">
                          {issue.categories.map(c => (
                            <span key={c} className="inline-block mr-1 px-2 py-0.5 bg-yellow-100 text-yellow-700 rounded text-xs">
                              {categoryLabel[c] || c}
                            </span>
                          ))}
                        </td>
                        <td className="px-4 py-2">
                          <span className="text-xs text-[var(--color-text-muted)]">待处理</span>
                        </td>
                      </tr>
                    ))}
                    {preMarked.map((pm, i) => (
                      <tr key={`pm-${i}`} className="border-b border-[var(--color-border)] last:border-b-0 bg-orange-50/50">
                        <td className="px-4 py-2">{pm.page_title}</td>
                        <td className="px-4 py-2">
                          {pm.anomaly_types.split(',').map((t: string) => (
                            <span key={t} className="inline-block mr-1 px-2 py-0.5 bg-orange-100 text-orange-700 rounded text-xs">
                              {categoryLabel[t] || t}
                            </span>
                          ))}
                        </td>
                        <td className="px-4 py-2">
                          <span className="text-xs text-orange-600 font-medium">已标记</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* 修复/标记按钮 */}
              {issues.length > 0 && (
                <div className="flex items-center gap-3">
                  <button onClick={() => setConfirmDialog('repair')} disabled={loading}
                    className="px-5 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm hover:opacity-90 disabled:opacity-50">
                    立即修复
                  </button>
                  <button onClick={() => setConfirmDialog('mark')} disabled={loading}
                    className="px-5 py-2 border border-orange-400 text-orange-600 rounded-lg text-sm hover:bg-orange-50 disabled:opacity-50">
                    仅标记异常
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* 确认对话框 */}
      {confirmDialog && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setConfirmDialog(null)}>
          <div className="bg-[var(--color-bg)] rounded-lg shadow-xl p-6 max-w-md w-full mx-4" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-medium mb-3">
              {confirmDialog === 'repair' ? '确认修复' : '确认标记'}
            </h3>
            <p className="text-sm text-[var(--color-text-muted)] mb-5">
              {confirmDialog === 'repair'
                ? `将自动修复 ${issues.length} 个复杂问题（或phan关联、empty补写、duplicate去重）。确定开始修复？`
                : `将 ${issues.length} 个页面标记为「复杂异常」但不修复，下次可通过扫描再次确认修复。确定仅标记？`}
            </p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setConfirmDialog(null)}
                className="px-4 py-2 border border-[var(--color-border)] rounded-lg text-sm">取消</button>
              <button onClick={confirmDialog === 'repair' ? doRepair : doMark} disabled={loading}
                className={`px-5 py-2 rounded-lg text-sm text-white disabled:opacity-50 ${
                  confirmDialog === 'repair'
                    ? 'bg-[var(--color-primary)] hover:opacity-90'
                    : 'bg-orange-500 hover:bg-orange-600'
                }`}>
                {loading ? '处理中...' : confirmDialog === 'repair' ? '是，开始修复' : '是，仅标记'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 修复结果 */}
      {repairResult && (
        <div className="mt-4">
          <h3 className="text-sm font-medium mb-3">修复结果</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            {[
              ['孤立→关联', repairResult.orphan_fixed ?? 0],
              ['空洞→补写', repairResult.empty_fixed ?? 0],
              ['重复→去重', repairResult.duplicate_fixed ?? 0],
              ['失败', repairResult.errors ?? 0],
            ].map(([label, val]) => (
              <div key={label as string} className="p-3 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)] text-center">
                <div className="text-2xl font-bold text-[var(--color-primary)]">{val as number}</div>
                <div className="text-xs text-[var(--color-text-muted)] mt-1">{label}</div>
              </div>
            ))}
          </div>
          <div className="p-3 rounded-lg text-sm bg-green-50 text-green-700">
            修复完成。{repairResult.status === 'clean' ? '无复杂问题' : `已处理 ${((repairResult.orphan_fixed ?? 0) + (repairResult.empty_fixed ?? 0) + (repairResult.duplicate_fixed ?? 0))} 项。`}
          </div>
        </div>
      )}
    </div>
  )
}
