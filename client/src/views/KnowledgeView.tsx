import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet, apiDelete } from '../api'
import { useToast } from '../components/Toast'
import PageHeader from '../components/PageHeader'
import DataTable, { type Column } from '../components/DataTable'
import { usePagination } from '../hooks/usePagination'
import { safeTags } from '../utils/helpers'

interface KnowledgeItem {
  id: string
  title: string
  content: string
  file_type: string
  tags: string
  created_at: string
  updated_at: string
  [key: string]: unknown
}

export default function KnowledgeView() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const pagination = usePagination(20)

  const fetchItems = useCallback(async (page?: number) => {
    setLoading(true)
    try {
      const p = page ?? pagination.page
      const params = new URLSearchParams({
        page: String(p),
        page_size: String(pagination.pageSize),
      })
      const path = search.trim()
        ? `/api/knowledge/search?q=${encodeURIComponent(search.trim())}&${params}`
        : `/api/knowledge?${params}`
      const data = await apiGet<{ items?: KnowledgeItem[]; total?: number } | KnowledgeItem[]>(path)
      const list = Array.isArray(data) ? data : data.items || []
      setItems(list)
      if (!Array.isArray(data) && data.total !== undefined) {
        pagination.setTotal(data.total)
      }
    } catch {
      toast('加载知识列表失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [pagination.page, pagination.pageSize, search, toast])

  useEffect(() => { fetchItems() }, [fetchItems])

  const handleSearch = () => fetchItems(1)

  const handleDelete = async (id: string) => {
    if (!confirm('确定删除此知识条目？')) return
    try {
      await apiDelete(`/api/knowledge/${id}`)
      toast('已删除', 'success')
      fetchItems()
    } catch {
      toast('删除失败', 'error')
    }
  }

  const columns: Column<KnowledgeItem>[] = [
    {
      key: 'title',
      title: '标题',
      render: row => (
        <button
          onClick={() => navigate(`/knowledge/${row.id}`)}
          className="text-[var(--color-primary)] hover:underline font-medium text-left"
        >
          {row.title}
        </button>
      ),
    },
    {
      key: 'file_type',
      title: '类型',
      className: 'w-20',
      render: row => (
        <span className="text-xs px-2 py-0.5 bg-[var(--color-border)] rounded">{row.file_type}</span>
      ),
    },
    {
      key: 'tags',
      title: '标签',
      render: row => (
        <div className="flex gap-1 flex-wrap">
          {safeTags(row.tags).map((t: string) => (
            <span key={t} className="text-xs px-1.5 py-0.5 bg-[var(--color-primary)]/10 text-[var(--color-primary)] rounded">
              {t}
            </span>
          ))}
        </div>
      ),
    },
    {
      key: 'updated_at',
      title: '更新时间',
      className: 'w-36',
      render: row => <span className="text-[var(--color-text-muted)]">{row.updated_at?.slice(0, 16)}</span>,
    },
    {
      key: 'actions',
      title: '',
      className: 'w-24',
      render: row => (
        <div className="flex gap-2">
          <button
            onClick={() => navigate(`/knowledge/${row.id}`)}
            className="text-xs text-[var(--color-primary)] hover:underline"
          >
            详情
          </button>
          <button
            onClick={() => handleDelete(row.id)}
            className="text-xs text-[var(--color-danger)] hover:underline"
          >
            删除
          </button>
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="知识库管理"
        actions={
          <>
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
              placeholder="搜索知识..."
              className="px-3 py-1.5 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm w-60"
            />
            <button onClick={handleSearch} className="px-4 py-1.5 bg-[var(--color-primary)] text-white rounded-lg text-sm hover:opacity-90">
              搜索
            </button>
            <button
              onClick={() => navigate('/import')}
              className="px-4 py-1.5 border border-[var(--color-primary)] text-[var(--color-primary)] rounded-lg text-sm hover:bg-[var(--color-primary-soft)]"
            >
              + 导入
            </button>
          </>
        }
      />

      <DataTable
        columns={columns}
        data={items}
        loading={loading}
        emptyText="暂无知识条目"
        rowKey={row => row.id}
        footer={
          pagination.total > pagination.pageSize ? (
            <div className="flex items-center justify-between">
              <span className="text-sm text-[var(--color-text-muted)]">
                共 {pagination.total} 条 · 第 {pagination.page}/{pagination.totalPages} 页
              </span>
              <div className="flex gap-2">
                <button onClick={pagination.prevPage} disabled={!pagination.hasPrev} className="px-3 py-1 border border-[var(--color-border)] rounded text-sm disabled:opacity-30">
                  上一页
                </button>
                <button onClick={pagination.nextPage} disabled={!pagination.hasNext} className="px-3 py-1 border border-[var(--color-border)] rounded text-sm disabled:opacity-30">
                  下一页
                </button>
              </div>
            </div>
          ) : undefined
        }
      />
    </div>
  )
}
