import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { apiGet, apiDelete } from '../api'
import { useToast } from '../components/Toast'
import PageHeader from '../components/PageHeader'
import { safeTags } from '../utils/helpers'

interface KnowledgeDetail {
  id: string
  title: string
  content: string
  file_type: string
  tags: string
  source: string
  created_at: string
  updated_at: string
  blocks?: Block[]
  [key: string]: unknown
}

interface Block {
  id: string
  block_type: string
  content: string
  heading?: string
  page?: number
  sequence: number
}

export default function KnowledgeDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { toast } = useToast()
  const [item, setItem] = useState<KnowledgeDetail | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!id) return
    apiGet<KnowledgeDetail>(`/api/knowledge/${id}`)
      .then(setItem)
      .catch(() => toast('加载知识详情失败', 'error'))
      .finally(() => setLoading(false))
  }, [id])

  const handleDelete = async () => {
    if (!item || !confirm('确定删除？此操作不可恢复')) return
    try {
      await apiDelete(`/api/knowledge/${item.id}`)
      toast('已删除', 'success')
      navigate('/knowledge', { replace: true })
    } catch {
      toast('删除失败', 'error')
    }
  }

  if (loading) return <div className="py-10 text-center text-[var(--color-text-muted)]">加载中...</div>
  if (!item) return <div className="py-10 text-center text-[var(--color-text-muted)]">未找到该知识条目</div>

  return (
    <div>
      <PageHeader
        title={item.title}
        subtitle={`${item.file_type} · ${item.source || '未知来源'}`}
        actions={
          <>
            <button onClick={() => navigate('/knowledge')} className="px-4 py-1.5 border border-[var(--color-border)] rounded-lg text-sm">
              返回列表
            </button>
            <button onClick={handleDelete} className="px-4 py-1.5 bg-[var(--color-danger)] text-white rounded-lg text-sm hover:opacity-90">
              删除
            </button>
          </>
        }
      />

      {/* 元信息 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
        <div className="p-3 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
          <div className="text-xs text-[var(--color-text-muted)]">类型</div>
          <div className="mt-1 text-sm font-medium">{item.file_type}</div>
        </div>
        <div className="p-3 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
          <div className="text-xs text-[var(--color-text-muted)]">来源</div>
          <div className="mt-1 text-sm font-medium truncate">{item.source || '-'}</div>
        </div>
        <div className="p-3 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
          <div className="text-xs text-[var(--color-text-muted)]">创建时间</div>
          <div className="mt-1 text-sm">{item.created_at?.slice(0, 16)}</div>
        </div>
        <div className="p-3 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
          <div className="text-xs text-[var(--color-text-muted)]">更新时间</div>
          <div className="mt-1 text-sm">{item.updated_at?.slice(0, 16)}</div>
        </div>
      </div>

      {/* 标签 */}
      {safeTags(item.tags).length > 0 && (
        <div className="mb-6 flex gap-2 flex-wrap">
          {safeTags(item.tags).map((t: string) => (
            <span key={t} className="text-xs px-2 py-1 bg-[var(--color-primary)]/10 text-[var(--color-primary)] rounded">
              {t}
            </span>
          ))}
        </div>
      )}

      {/* 内容 */}
      <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)] p-5 mb-6">
        <h3 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">内容预览</h3>
        <div className="whitespace-pre-wrap text-sm max-h-[400px] overflow-auto">{item.content}</div>
      </div>

      {/* Blocks */}
      {item.blocks && item.blocks.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">内容块 ({item.blocks.length})</h3>
          <div className="space-y-2">
            {item.blocks.map(block => (
              <div key={block.id} className="p-3 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs px-1.5 py-0.5 bg-[var(--color-border)] rounded">{block.block_type}</span>
                  {block.heading && <span className="text-xs font-medium">{block.heading}</span>}
                  {block.page !== undefined && <span className="text-xs text-[var(--color-text-muted)]">P{block.page}</span>}
                  <span className="text-xs text-[var(--color-text-muted)] ml-auto">#{block.sequence}</span>
                </div>
                <div className="text-sm text-[var(--color-text-muted)] line-clamp-3">{block.content}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
