import React, { useEffect, useState } from 'react'

interface KnowledgeItem {
  id: string
  title: string
  content: string
  file_type: string
  tags: string
  created_at: string
  updated_at: string
}

export default function KnowledgeView() {
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')

  useEffect(() => {
    fetchItems()
  }, [])

  const fetchItems = async () => {
    try {
      const res = await fetch('/api/knowledge?limit=50')
      const data = await res.json()
      setItems(data.items || data || [])
    } catch (err) {
      console.error('Failed to fetch knowledge:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleSearch = async () => {
    if (!search.trim()) return fetchItems()
    setLoading(true)
    try {
      const res = await fetch(`/api/knowledge/search?q=${encodeURIComponent(search)}`)
      const data = await res.json()
      setItems(data.items || data || [])
    } catch (err) {
      console.error('Search failed:', err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold">知识库管理</h2>
        <div className="flex gap-2">
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="搜索知识..."
            className="px-3 py-1.5 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm"
          />
          <button
            onClick={handleSearch}
            className="px-4 py-1.5 bg-[var(--color-primary)] text-white rounded-lg text-sm hover:opacity-90"
          >
            搜索
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-[var(--color-text-muted)]">加载中...</div>
      ) : items.length === 0 ? (
        <div className="text-[var(--color-text-muted)]">暂无知识条目</div>
      ) : (
        <div className="grid gap-3">
          {items.map(item => (
            <div key={item.id} className="p-4 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
              <div className="flex items-center justify-between">
                <h3 className="font-medium">{item.title}</h3>
                <span className="text-xs text-[var(--color-text-muted)] px-2 py-0.5 bg-[var(--color-border)] rounded">
                  {item.file_type}
                </span>
              </div>
              <p className="mt-2 text-sm text-[var(--color-text-muted)] line-clamp-2">
                {item.content?.slice(0, 200)}
              </p>
              {item.tags && (
                <div className="mt-2 flex gap-1 flex-wrap">
                  {JSON.parse(item.tags || '[]').map((tag: string) => (
                    <span key={tag} className="text-xs px-2 py-0.5 bg-[var(--color-primary)]/20 text-[var(--color-accent)] rounded">
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
