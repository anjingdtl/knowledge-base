import { useState, useRef, useCallback, useEffect } from 'react'
import { apiPost, apiUpload, apiGet } from '../api'
import { useToast } from '../components/Toast'
import PageHeader from '../components/PageHeader'

interface ImportJob {
  id: string
  status: string
  type: string
  metadata?: Record<string, string>
  created_at: string
}

export default function ImportView() {
  const { toast } = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [mode, setMode] = useState<'file' | 'url'>('file')
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const [recentJobs, setRecentJobs] = useState<ImportJob[]>([])
  const [polling, setPolling] = useState(false)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    return () => {
      mountedRef.current = false
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    }
  }, [])

  const pollJobs = useCallback(async () => {
    try {
      const data = await apiGet<{ jobs: ImportJob[] }>('/api/jobs?limit=10')
      if (!mountedRef.current) return
      setRecentJobs(data.jobs || [])
      const hasRunning = (data.jobs || []).some(j => j.status === 'running' || j.status === 'pending')
      if (hasRunning) {
        setPolling(true)
        pollTimerRef.current = setTimeout(pollJobs, 3000)
      } else {
        setPolling(false)
      }
    } catch { /* ignore */ }
  }, [])

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setLoading(true)
    try {
      for (const file of Array.from(files)) {
        await apiUpload<ImportJob>('/api/knowledge/import', file)
      }
      toast(`成功提交 ${files.length} 个文件`, 'success')
      pollJobs()
    } catch (err) {
      toast(err instanceof Error ? err.message : '导入失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleUrlImport = async () => {
    if (!url.trim()) return
    setLoading(true)
    try {
      await apiPost<ImportJob>('/api/knowledge/import-url', { url: url.trim() })
      toast('URL 导入任务已提交', 'success')
      setUrl('')
      pollJobs()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'URL 导入失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragActive(false)
    handleFiles(e.dataTransfer.files)
  }

  return (
    <div>
      <PageHeader title="导入中心" subtitle="导入文件或 URL 到知识库" />

      {/* 模式切换 */}
      <div className="flex gap-1 mb-6 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-0.5 w-fit">
        {([['file', '文件上传'], ['url', 'URL 导入']] as const).map(([k, l]) => (
          <button
            key={k}
            onClick={() => setMode(k)}
            className={`px-4 py-1.5 rounded-md text-sm transition-colors ${
              mode === k ? 'bg-[var(--color-primary)] text-white' : 'text-[var(--color-text-muted)] hover:text-[var(--color-primary)]'
            }`}
          >
            {l}
          </button>
        ))}
      </div>

      {mode === 'file' ? (
        <div
          onDragOver={e => { e.preventDefault(); setDragActive(true) }}
          onDragLeave={() => setDragActive(false)}
          onDrop={handleDrop}
          onClick={() => fileRef.current?.click()}
          className={`p-10 border-2 border-dashed rounded-lg text-center cursor-pointer transition-colors ${
            dragActive ? 'border-[var(--color-primary)] bg-[var(--color-primary-soft)]' : 'border-[var(--color-border)] hover:border-[var(--color-primary)]'
          }`}
        >
          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".txt,.md,.pdf,.docx,.xlsx,.pptx,.csv,.json,.html,.htm"
            onChange={e => handleFiles(e.target.files)}
            className="hidden"
          />
          <div className="text-3xl mb-2">📁</div>
          <div className="text-sm font-medium">
            {loading ? '上传中...' : '拖拽文件到此处，或点击选择文件'}
          </div>
          <div className="mt-2 text-xs text-[var(--color-text-muted)]">
            支持 PDF、Word、Excel、PPT、Markdown、TXT、CSV、JSON 等格式
          </div>
        </div>
      ) : (
        <div className="max-w-lg space-y-3">
          <input
            value={url}
            onChange={e => setUrl(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleUrlImport()}
            placeholder="输入网页 URL..."
            className="w-full px-4 py-2.5 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm"
          />
          <button
            onClick={handleUrlImport}
            disabled={loading || !url.trim()}
            className="px-6 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm disabled:opacity-50"
          >
            {loading ? '导入中...' : '开始导入'}
          </button>
        </div>
      )}

      {/* 最近导入任务 */}
      {recentJobs.length > 0 && (
        <div className="mt-8">
          <h3 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">
            最近导入任务 {polling && <span className="animate-pulse text-[var(--color-primary)]">●</span>}
          </h3>
          <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg)]">
                  <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">类型</th>
                  <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">状态</th>
                  <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">详情</th>
                  <th className="px-4 py-2 text-left font-medium text-[var(--color-text-muted)]">时间</th>
                </tr>
              </thead>
              <tbody>
                {recentJobs.map(job => (
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
                    <td className="px-4 py-2 text-[var(--color-text-muted)] truncate max-w-xs">
                      {job.metadata?.filename || job.metadata?.url || '-'}
                    </td>
                    <td className="px-4 py-2 text-[var(--color-text-muted)]">{job.created_at?.slice(0, 16)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
