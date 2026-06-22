import { useEffect, useState } from 'react'
import { apiGet, apiPost, getToken } from '../api'
import { useToast } from '../components/Toast'
import PageHeader from '../components/PageHeader'

interface Settings {
  llm: { model: string; api_base: string; api_key_set: boolean }
  embedding: { model: string; api_base: string; api_key_set: boolean }
  reranker: { model: string; api_base: string; api_key_set: boolean }
  mcp: { write_policy: string; allow_http_write: boolean; bind_host: string }
  graph_backend: { type: string; uri: string; user: string; password_set: boolean }
}

type Tab = 'models' | 'mcp' | 'backup'

export default function SettingsView() {
  const [tab, setTab] = useState<Tab>('models')
  const { toast } = useToast()

  return (
    <div>
      <PageHeader title="设置" subtitle="系统配置与管理" />

      <div className="flex gap-1 mb-6 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-0.5 w-fit">
        {([['models', '模型配置'], ['mcp', 'MCP 安全'], ['backup', '数据管理']] as [Tab, string][]).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`px-4 py-1.5 rounded-md text-sm transition-colors ${
              tab === k ? 'bg-[var(--color-primary)] text-white' : 'text-[var(--color-text-muted)] hover:text-[var(--color-primary)]'
            }`}
          >
            {l}
          </button>
        ))}
      </div>

      {tab === 'models' && <ModelsTab />}
      {tab === 'mcp' && <McpTab />}
      {tab === 'backup' && <BackupTab />}
    </div>
  )
}

function ModelsTab() {
  const { toast } = useToast()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  // Editable fields
  const [llmModel, setLlmModel] = useState('')
  const [llmBase, setLlmBase] = useState('')
  const [embModel, setEmbModel] = useState('')
  const [embBase, setEmbBase] = useState('')
  const [rerankModel, setRerankModel] = useState('')

  useEffect(() => {
    apiGet<Settings>('/api/settings')
      .then(data => {
        setSettings(data)
        setLlmModel(data.llm?.model || '')
        setLlmBase(data.llm?.api_base || '')
        setEmbModel(data.embedding?.model || '')
        setEmbBase(data.embedding?.api_base || '')
        setRerankModel(data.reranker?.model || '')
      })
      .catch(() => {
        // Settings endpoint may not exist yet — show defaults
        setSettings(null)
      })
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await apiPost('/api/settings', {
        llm: { model: llmModel, api_base: llmBase },
        embedding: { model: embModel, api_base: embBase },
        reranker: { model: rerankModel },
      })
      toast('设置已保存', 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : '保存失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="text-[var(--color-text-muted)]">加载设置...</div>

  return (
    <div className="max-w-2xl space-y-6">
      {/* LLM */}
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-5">
        <h3 className="font-medium mb-3">LLM 配置</h3>
        <div className="space-y-3">
          <div>
            <label className="block text-sm text-[var(--color-text-muted)] mb-1">模型名称</label>
            <input value={llmModel} onChange={e => setLlmModel(e.target.value)}
              className="w-full px-3 py-2 bg-[var(--color-input)] border border-[var(--color-border)] rounded-lg text-sm" />
          </div>
          <div>
            <label className="block text-sm text-[var(--color-text-muted)] mb-1">API 地址</label>
            <input value={llmBase} onChange={e => setLlmBase(e.target.value)}
              className="w-full px-3 py-2 bg-[var(--color-input)] border border-[var(--color-border)] rounded-lg text-sm" />
          </div>
          <div>
            <label className="block text-sm text-[var(--color-text-muted)] mb-1">API Key</label>
            <input type="password" disabled value={settings?.llm?.api_key_set ? '••••••••' : ''}
              className="w-full px-3 py-2 bg-[var(--color-input)] border border-[var(--color-border)] rounded-lg text-sm disabled:opacity-50"
              placeholder="通过环境变量或 config.yaml 配置" />
          </div>
        </div>
      </div>

      {/* Embedding */}
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-5">
        <h3 className="font-medium mb-3">Embedding 配置</h3>
        <div className="space-y-3">
          <div>
            <label className="block text-sm text-[var(--color-text-muted)] mb-1">模型名称</label>
            <input value={embModel} onChange={e => setEmbModel(e.target.value)}
              className="w-full px-3 py-2 bg-[var(--color-input)] border border-[var(--color-border)] rounded-lg text-sm" />
          </div>
          <div>
            <label className="block text-sm text-[var(--color-text-muted)] mb-1">API 地址</label>
            <input value={embBase} onChange={e => setEmbBase(e.target.value)}
              className="w-full px-3 py-2 bg-[var(--color-input)] border border-[var(--color-border)] rounded-lg text-sm" />
          </div>
        </div>
      </div>

      {/* Reranker */}
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-5">
        <h3 className="font-medium mb-3">Reranker 配置</h3>
        <div>
          <label className="block text-sm text-[var(--color-text-muted)] mb-1">模型名称</label>
          <input value={rerankModel} onChange={e => setRerankModel(e.target.value)}
            className="w-full px-3 py-2 bg-[var(--color-input)] border border-[var(--color-border)] rounded-lg text-sm" />
        </div>
      </div>

      {/* Graph Storage */}
      {settings?.graph_backend && (
        <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-5">
          <h3 className="font-medium mb-3">图后端</h3>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div><span className="text-[var(--color-text-muted)]">类型：</span>{settings.graph_backend.type}</div>
            <div><span className="text-[var(--color-text-muted)]">URI：</span>{settings.graph_backend.uri}</div>
            <div><span className="text-[var(--color-text-muted)]">用户：</span>{settings.graph_backend.user}</div>
            <div><span className="text-[var(--color-text-muted)]">密码：</span>{settings.graph_backend.password_set ? '已配置' : '未配置'}</div>
          </div>
        </div>
      )}

      <button onClick={handleSave} disabled={saving}
        className="px-6 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm disabled:opacity-50">
        {saving ? '保存中...' : '保存设置'}
      </button>
    </div>
  )
}

function McpTab() {
  const { toast } = useToast()
  const [policy, setPolicy] = useState('preview_only')
  const [allowHttp, setAllowHttp] = useState(false)
  const [saving, setSaving] = useState(false)

  const policies = [
    { value: 'preview_only', label: '仅预览（推荐）', desc: '写操作只返回预览结果，不实际执行' },
    { value: 'local_confirm', label: '本地确认', desc: '需要本地 MCP 客户端确认后执行' },
    { value: 'token_required', label: 'Token 验证', desc: 'HTTP 模式需要提供 auth_token' },
    { value: 'disabled', label: '禁用写操作', desc: '所有写操作被禁止' },
  ]

  const handleSave = async () => {
    setSaving(true)
    try {
      await apiPost('/api/settings/mcp', { write_policy: policy, allow_http_write: allowHttp })
      toast('MCP 安全设置已保存', 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : '保存失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-5">
        <h3 className="font-medium mb-3">写操作策略</h3>
        <div className="space-y-3">
          {policies.map(p => (
            <label key={p.value} className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
              policy === p.value ? 'border-[var(--color-primary)] bg-[var(--color-primary-soft)]' : 'border-[var(--color-border)] hover:border-[var(--color-primary)]'
            }`}>
              <input type="radio" name="policy" value={p.value} checked={policy === p.value}
                onChange={() => setPolicy(p.value)} className="mt-0.5" />
              <div>
                <div className="text-sm font-medium">{p.label}</div>
                <div className="text-xs text-[var(--color-text-muted)]">{p.desc}</div>
              </div>
            </label>
          ))}
        </div>
      </div>

      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-5">
        <label className="flex items-center gap-3 cursor-pointer">
          <input type="checkbox" checked={allowHttp} onChange={e => setAllowHttp(e.target.checked)}
            className="w-4 h-4" />
          <div>
            <div className="text-sm font-medium">允许 HTTP 写操作</div>
            <div className="text-xs text-[var(--color-text-muted)]">默认 HTTP 传输模式下禁用写操作</div>
          </div>
        </label>
      </div>

      <button onClick={handleSave} disabled={saving}
        className="px-6 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm disabled:opacity-50">
        {saving ? '保存中...' : '保存安全设置'}
      </button>
    </div>
  )
}

function BackupTab() {
  const { toast } = useToast()
  const [loading, setLoading] = useState(false)

  const handleBackup = async () => {
    setLoading(true)
    try {
      await apiPost('/api/settings/backup', {})
      toast('备份已创建', 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : '备份失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleExport = async () => {
    try {
      const res = await fetch('/api/settings/export', {
        headers: { 'Authorization': `Bearer ${getToken()}` },
      })
      if (!res.ok) throw new Error('导出失败')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `knowledge-backup-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
      toast('导出成功', 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : '导出失败', 'error')
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-5">
        <h3 className="font-medium mb-3">数据备份</h3>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">
          创建当前知识库的完整备份，包括所有知识条目、Wiki 页面、对话记录和 Agent 记忆。
        </p>
        <div className="flex gap-3">
          <button onClick={handleBackup} disabled={loading}
            className="px-5 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm disabled:opacity-50">
            {loading ? '备份中...' : '创建备份'}
          </button>
          <button onClick={handleExport}
            className="px-5 py-2 border border-[var(--color-primary)] text-[var(--color-primary)] rounded-lg text-sm hover:bg-[var(--color-primary-soft)]">
            导出 JSON
          </button>
        </div>
      </div>

      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-5">
        <h3 className="font-medium mb-3">系统信息</h3>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="text-[var(--color-text-muted)]">数据目录</div>
          <div>data/</div>
          <div className="text-[var(--color-text-muted)]">数据库</div>
          <div>SQLite + FTS5</div>
          <div className="text-[var(--color-text-muted)]">向量引擎</div>
          <div>sqlite-vec</div>
          <div className="text-[var(--color-text-muted)]">图谱存储</div>
          <div>SQLite 内置图索引</div>
        </div>
      </div>
    </div>
  )
}
