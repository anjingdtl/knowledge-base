import { useState, useEffect, useCallback } from 'react'
import { apiGet, apiPost, apiDelete } from '../api'
import { useToast } from '../components/Toast'

interface Session {
  id: string
  status: string
  total_items_scanned: number
  candidates_found: number
  pairs_judged: number
  pairs_deleted: number
  pairs_ignored: number
  started_at: string
  completed_at: string | null
}

interface Pair {
  id: string
  item_a_id: string
  item_b_id: string
  item_a_title: string | null
  item_b_title: string | null
  item_a_created: string | null
  item_b_created: string | null
  candidate_source: string
  similarity_score: number | null
  relation_type: string | null
  newer_item_id: string | null
  confidence: number | null
  reason: string | null
  status: string
}

interface Ignore {
  id: string
  item_a_id: string
  item_b_id: string
  item_a_title: string | null
  item_b_title: string | null
  ignored_at: string
}

const POLL_INTERVAL_MS = 2000

export default function MaintenanceView() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [currentSession, setCurrentSession] = useState<Session | null>(null)
  const [pairs, setPairs] = useState<Pair[]>([])
  const [ignores, setIgnores] = useState<Ignore[]>([])
  const [statusFilter, setStatusFilter] = useState<string>('pending')
  const [expandedPair, setExpandedPair] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const { toast } = useToast()

  const loadSessions = useCallback(async () => {
    try {
      const data = await apiGet<{ sessions: Session[] }>('/api/maintenance/version-conflict/sessions')
      setSessions(data.sessions || [])
    } catch {
      toast('加载会话失败', 'error')
    }
  }, [toast])

  const loadPairs = useCallback(async (sessionId: string) => {
    try {
      const params = new URLSearchParams()
      if (statusFilter) params.set('status', statusFilter)
      params.set('limit', '50')
      const qs = params.toString()
      const path = `/api/maintenance/version-conflict/sessions/${sessionId}/pairs${qs ? '?' + qs : ''}`
      const data = await apiGet<{ pairs: Pair[] }>(path)
      setPairs(data.pairs || [])
    } catch {
      toast('加载候选对失败', 'error')
    }
  }, [statusFilter, toast])

  const loadIgnores = useCallback(async () => {
    try {
      const data = await apiGet<{ ignores: Ignore[] }>('/api/maintenance/version-conflict/ignores')
      setIgnores(data.ignores || [])
    } catch {
      toast('加载忽略列表失败', 'error')
    }
  }, [toast])

  // 轮询当前会话状态
  useEffect(() => {
    if (!currentSession || ['ready', 'completed', 'error'].includes(currentSession.status)) {
      return
    }
    const timer = setInterval(async () => {
      try {
        const s = await apiGet<Session>(`/api/maintenance/version-conflict/sessions/${currentSession.id}`)
        setCurrentSession(s)
        if (s.status === 'ready') {
          loadPairs(s.id)
        }
      } catch {
        // ignore polling errors
      }
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [currentSession, loadPairs])

  useEffect(() => {
    loadSessions()
    loadIgnores()
  }, [loadSessions, loadIgnores])

  useEffect(() => {
    if (currentSession) {
      loadPairs(currentSession.id)
    }
  }, [currentSession?.id, statusFilter, loadPairs])

  const handleStartScan = async () => {
    if (!confirm('开始新扫描？已忽略的对将不会被扫描。')) return
    setLoading(true)
    try {
      const data = await apiPost<{ session_id: string }>('/api/maintenance/version-conflict/sessions', { rescan_ignored: false })
      toast('扫描已启动', 'success')
      const s = await apiGet<Session>(`/api/maintenance/version-conflict/sessions/${data.session_id}`)
      setCurrentSession(s)
      loadSessions()
    } catch {
      toast('启动扫描失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleJudge = async (sessionId: string) => {
    try {
      await apiPost(`/api/maintenance/version-conflict/sessions/${sessionId}/judge?limit=20`, {})
      toast('判断任务已触发', 'success')
    } catch {
      toast('触发判断失败', 'error')
    }
  }

  const handleDelete = async (pairId: string, pair: Pair) => {
    const olderTitle = pair.newer_item_id === pair.item_a_id
      ? pair.item_b_title
      : pair.item_a_title
    const newerTitle = pair.newer_item_id === pair.item_a_id
      ? pair.item_a_title
      : pair.item_b_title
    if (!confirm(`将删除旧版 [${olderTitle}]，新版 [${newerTitle}] 保留。确认？`)) return
    try {
      await apiPost(`/api/maintenance/version-conflict/pairs/${pairId}/delete`, { operator: 'user' })
      toast('已删除旧版本', 'success')
      if (currentSession) loadPairs(currentSession.id)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '删除失败'
      toast(msg, 'error')
    }
  }

  const handleIgnore = async (pairId: string) => {
    try {
      await apiPost(`/api/maintenance/version-conflict/pairs/${pairId}/ignore`, {})
      toast('已忽略', 'success')
      if (currentSession) loadPairs(currentSession.id)
      loadIgnores()
    } catch {
      toast('忽略失败', 'error')
    }
  }

  const handleRejudge = async (pairId: string) => {
    try {
      await apiPost(`/api/maintenance/version-conflict/pairs/${pairId}/judge`, {})
      toast('已重新判断', 'success')
      if (currentSession) loadPairs(currentSession.id)
    } catch {
      toast('重新判断失败', 'error')
    }
  }

  const handleUndoIgnore = async (ignoreId: string) => {
    if (!confirm('撤销忽略？下次扫描会重新判断。')) return
    try {
      await apiDelete(`/api/maintenance/version-conflict/ignores/${ignoreId}`)
      toast('已撤销忽略', 'success')
      loadIgnores()
    } catch {
      toast('撤销失败', 'error')
    }
  }

  const relationLabel = (rt: string | null) => {
    const map: Record<string, string> = {
      supersedes: 'A替代B',
      superseded_by: 'B替代A',
      partial_overlap: '部分重叠',
      unrelated: '无关',
    }
    return rt ? (map[rt] || rt) : '未判断'
  }

  const canDelete = (pair: Pair) => {
    return pair.relation_type === 'supersedes' || pair.relation_type === 'superseded_by'
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">维护中心</h1>
        <button
          onClick={handleStartScan}
          disabled={loading}
          className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:opacity-90 disabled:opacity-50"
        >
          {loading ? '启动中...' : '开始新扫描'}
        </button>
      </div>

      {/* 当前会话进度 */}
      {currentSession && (
        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
          <div className="flex items-center justify-between mb-2">
            <span className="font-medium">当前会话</span>
            <span className="text-sm text-[var(--color-text-muted)]">
              {currentSession.status}
            </span>
          </div>
          <div className="grid grid-cols-4 gap-4 text-sm">
            <div>扫描条目: {currentSession.total_items_scanned}</div>
            <div>候选对: {currentSession.candidates_found}</div>
            <div>已判断: {currentSession.pairs_judged}</div>
            <div>已删除: {currentSession.pairs_deleted}</div>
          </div>
          {currentSession.status === 'ready' && (
            <button
              onClick={() => handleJudge(currentSession.id)}
              className="mt-3 px-3 py-1 text-sm bg-[var(--color-accent)] text-white rounded"
            >
              触发 LLM 判断
            </button>
          )}
        </div>
      )}

      {/* 候选对列表 */}
      <div>
        <div className="flex items-center gap-3 mb-3">
          <h2 className="text-lg font-semibold">候选对</h2>
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            className="px-2 py-1 text-sm bg-[var(--color-surface)] border border-[var(--color-border)] rounded"
          >
            <option value="pending">待处理</option>
            <option value="ignored">已忽略</option>
            <option value="deleted">已删除</option>
            <option value="">全部</option>
          </select>
        </div>
        {pairs.length === 0 ? (
          <p className="text-[var(--color-text-muted)] text-sm">暂无候选对</p>
        ) : (
          <div className="space-y-2">
            {pairs.map(pair => (
              <div
                key={pair.id}
                className={`p-3 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)] ${
                  pair.status === 'deleted' ? 'opacity-50' : ''
                }`}
              >
                <div className="flex items-center justify-between gap-4">
                  <div className="flex-1 grid grid-cols-2 gap-4">
                    <div>
                      <div className="font-medium">{pair.item_a_title || '(已删除)'}</div>
                      <div className="text-xs text-[var(--color-text-muted)]">
                        {pair.item_a_created?.slice(0, 10)}
                      </div>
                    </div>
                    <div>
                      <div className="font-medium">{pair.item_b_title || '(已删除)'}</div>
                      <div className="text-xs text-[var(--color-text-muted)]">
                        {pair.item_b_created?.slice(0, 10)}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-medium">
                      {relationLabel(pair.relation_type)}
                    </div>
                    {pair.confidence != null && (
                      <div className="text-xs text-[var(--color-text-muted)]">
                        置信度: {(pair.confidence * 100).toFixed(0)}%
                      </div>
                    )}
                  </div>
                </div>
                {pair.reason && (
                  <p className="mt-2 text-sm text-[var(--color-text-muted)]">{pair.reason}</p>
                )}
                <div className="mt-2 flex gap-2">
                  <button
                    onClick={() => setExpandedPair(expandedPair === pair.id ? null : pair.id)}
                    className="px-2 py-1 text-xs bg-[var(--color-surface-hover)] rounded"
                  >
                    {expandedPair === pair.id ? '收起' : '查看详情'}
                  </button>
                  {canDelete(pair) && pair.status === 'pending' && (
                    <button
                      onClick={() => handleDelete(pair.id, pair)}
                      className="px-2 py-1 text-xs bg-red-500 text-white rounded"
                    >
                      确认删除旧版
                    </button>
                  )}
                  {pair.relation_type === 'partial_overlap' && (
                    <span className="px-2 py-1 text-xs text-[var(--color-text-muted)]">
                      部分重叠，需手动处理
                    </span>
                  )}
                  {pair.status === 'pending' && (
                    <>
                      <button
                        onClick={() => handleIgnore(pair.id)}
                        className="px-2 py-1 text-xs bg-[var(--color-surface-hover)] rounded"
                      >
                        忽略
                      </button>
                      <button
                        onClick={() => handleRejudge(pair.id)}
                        className="px-2 py-1 text-xs bg-[var(--color-surface-hover)] rounded"
                      >
                        重新判断
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 历史会话 */}
      <details className="border border-[var(--color-border)] rounded-lg">
        <summary className="p-3 cursor-pointer font-medium">历史会话 ({sessions.length})</summary>
        <div className="p-3 space-y-1">
          {sessions.map(s => (
            <button
              key={s.id}
              onClick={() => setCurrentSession(s)}
              className="w-full text-left p-2 hover:bg-[var(--color-surface-hover)] rounded text-sm"
            >
              <span className="font-mono">{s.id.slice(0, 8)}</span>
              <span className="ml-2 text-[var(--color-text-muted)]">{s.status}</span>
              <span className="ml-2 text-xs">
                候选 {s.candidates_found} / 删除 {s.pairs_deleted}
              </span>
            </button>
          ))}
        </div>
      </details>

      {/* 忽略列表 */}
      <details className="border border-[var(--color-border)] rounded-lg">
        <summary className="p-3 cursor-pointer font-medium">
          忽略列表 ({ignores.length})
        </summary>
        <div className="p-3 space-y-1">
          {ignores.length === 0 ? (
            <p className="text-sm text-[var(--color-text-muted)]">暂无忽略记录</p>
          ) : ignores.map(ig => (
            <div key={ig.id} className="flex items-center justify-between p-2 text-sm">
              <span>
                {ig.item_a_title || '(已删除)'} ↔ {ig.item_b_title || '(已删除)'}
              </span>
              <button
                onClick={() => handleUndoIgnore(ig.id)}
                className="px-2 py-1 text-xs bg-[var(--color-surface-hover)] rounded"
              >
                撤销忽略
              </button>
            </div>
          ))}
        </div>
      </details>
    </div>
  )
}
