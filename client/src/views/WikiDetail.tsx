import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { apiGet, apiPost, apiPut } from '../api'
import { useToast } from '../components/Toast'
import PageHeader from '../components/PageHeader'

interface WikiPage {
  id: string
  title: string
  content: string
  summary: string
  status: string
  tags: string
  version: number
  created_at: string
  updated_at: string
  [key: string]: unknown
}

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿', review: '审核中', published: '已发布', deprecated: '已废弃',
}

const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-yellow-100 text-yellow-700',
  review: 'bg-blue-100 text-blue-700',
  published: 'bg-green-100 text-green-700',
  deprecated: 'bg-gray-100 text-gray-500',
}

const WORKFLOW_ACTIONS: Record<string, { label: string; action: string; color: string }[]> = {
  draft: [
    { label: '提交审核', action: 'submit_review', color: 'bg-blue-600' },
  ],
  review: [
    { label: '批准发布', action: 'approve', color: 'bg-green-600' },
    { label: '驳回', action: 'reject', color: 'bg-red-600' },
  ],
  published: [
    { label: '标记废弃', action: 'deprecate', color: 'bg-gray-600' },
  ],
  deprecated: [],
}

export default function WikiDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { toast } = useToast()
  const [page, setPage] = useState<WikiPage | null>(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [editTitle, setEditTitle] = useState('')
  const [saving, setSaving] = useState(false)

  const loadPage = async () => {
    if (!id) return
    try {
      const data = await apiGet<WikiPage>(`/api/wiki/pages/${id}`)
      setPage(data)
      setEditContent(data.content || '')
      setEditTitle(data.title || '')
    } catch {
      toast('加载页面失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadPage() }, [id])

  const handleSave = async () => {
    if (!page || saving) return
    setSaving(true)
    try {
      await apiPut(`/api/wiki/pages/${page.id}`, {
        title: editTitle,
        content: editContent,
      })
      toast('保存成功', 'success')
      setEditing(false)
      loadPage()
    } catch (err) {
      toast(err instanceof Error ? err.message : '保存失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleWorkflow = async (action: string) => {
    if (!page) return
    try {
      await apiPost(`/api/wiki/pages/${page.id}/workflow`, { action })
      toast('操作成功', 'success')
      loadPage()
    } catch (err) {
      toast(err instanceof Error ? err.message : '操作失败', 'error')
    }
  }

  if (loading) return <div className="py-10 text-center text-[var(--color-text-muted)]">加载中...</div>
  if (!page) return <div className="py-10 text-center text-[var(--color-text-muted)]">未找到该页面</div>

  const actions = WORKFLOW_ACTIONS[page.status] || []

  return (
    <div>
      <PageHeader
        title={editing ? editTitle : page.title}
        subtitle={
          <span className="flex items-center gap-2">
            <span className={`text-xs px-2 py-0.5 rounded-full ${STATUS_COLORS[page.status] || ''}`}>
              {STATUS_LABELS[page.status] || page.status}
            </span>
            <span className="text-[var(--color-text-muted)]">v{page.version} · {page.updated_at?.slice(0, 16)}</span>
          </span>
        }
        actions={
          <>
            {!editing && (
              <button onClick={() => setEditing(true)} className="px-4 py-1.5 bg-[var(--color-primary)] text-white rounded-lg text-sm">
                编辑
              </button>
            )}
            {editing && (
              <>
                <button onClick={handleSave} disabled={saving} className="px-4 py-1.5 bg-[var(--color-primary)] text-white rounded-lg text-sm disabled:opacity-50">
                  {saving ? '保存中...' : '保存'}
                </button>
                <button onClick={() => { setEditing(false); setEditContent(page.content); setEditTitle(page.title) }} className="px-4 py-1.5 border border-[var(--color-border)] rounded-lg text-sm">
                  取消
                </button>
              </>
            )}
            <button onClick={() => navigate('/wiki')} className="px-4 py-1.5 border border-[var(--color-border)] rounded-lg text-sm">
              返回
            </button>
          </>
        }
      />

      {/* 工作流操作 */}
      {actions.length > 0 && (
        <div className="flex gap-2 mb-4">
          {actions.map(a => (
            <button key={a.action} onClick={() => handleWorkflow(a.action)} className={`px-4 py-1.5 ${a.color} text-white rounded-lg text-sm`}>
              {a.label}
            </button>
          ))}
        </div>
      )}

      {/* 内容 */}
      {editing ? (
        <div className="space-y-3">
          <input
            value={editTitle}
            onChange={e => setEditTitle(e.target.value)}
            className="w-full px-4 py-2 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm font-medium"
            placeholder="页面标题"
          />
          <textarea
            value={editContent}
            onChange={e => setEditContent(e.target.value)}
            className="w-full px-4 py-3 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm font-mono min-h-[500px] resize-y"
            placeholder="Markdown 内容..."
          />
        </div>
      ) : (
        <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-6">
          {page.summary && (
            <p className="text-sm text-[var(--color-text-muted)] mb-4 italic">{page.summary}</p>
          )}
          <div className="prose prose-sm max-w-none whitespace-pre-wrap">
            {page.content || '暂无内容'}
          </div>
        </div>
      )}
    </div>
  )
}
