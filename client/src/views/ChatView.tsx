import React, { useState, useRef, useEffect } from 'react'
import { apiPost } from '../api'

interface Diagnostics {
  route: { mode: string; explanation: string }
  retrieval: {
    total_sources: number
    wiki_hits: number
    graph_nodes: number
    graph_truncated: boolean
    evidence_chars: number
    evidence_tokens_est: number
  }
  query_plan: Record<string, unknown>
  dropped_candidates: { reason: string }[]
  warnings: string[]
}

interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: { title: string; knowledge_id: string; block_id?: string; snippet?: string; score?: number }[]
  diagnostics?: Diagnostics
}

function DiagnosticsPanel({ diagnostics }: { diagnostics: Diagnostics }) {
  const [expanded, setExpanded] = useState(false)
  const ret = diagnostics.retrieval

  return (
    <div className="mt-2 pt-2 border-t border-[var(--color-border)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-primary)] transition-colors"
      >
        <span className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>
        <span>检索诊断</span>
        <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-primary-soft)] text-[var(--color-primary)]">
          {diagnostics.route.mode}
        </span>
        <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-surface)] text-[var(--color-text-muted)]">
          {ret.total_sources} 来源 · ~{ret.evidence_tokens_est} tokens
        </span>
      </button>

      {expanded && (
        <div className="mt-2 space-y-2 text-xs text-[var(--color-text-muted)]">
          {/* 检索路由 */}
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] p-2">
              <div className="font-medium text-[var(--color-text)]">路由</div>
              <div>模式: <span className="text-[var(--color-primary)]">{diagnostics.route.mode}</span></div>
              {diagnostics.route.explanation && (
                <div className="text-[11px] mt-1">{diagnostics.route.explanation}</div>
              )}
            </div>
            <div className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] p-2">
              <div className="font-medium text-[var(--color-text)]">检索统计</div>
              <div>来源数: {ret.total_sources}</div>
              <div>Wiki 命中: {ret.wiki_hits}</div>
              <div>图谱节点: {ret.graph_nodes}{ret.graph_truncated ? ' (已截断)' : ''}</div>
              <div>证据长度: ~{ret.evidence_tokens_est} tokens</div>
            </div>
          </div>

          {/* 警告 */}
          {diagnostics.warnings.length > 0 && (
            <div className="rounded border border-yellow-500/30 bg-yellow-500/5 p-2">
              <div className="font-medium text-yellow-400">⚠ 警告</div>
              {diagnostics.warnings.map((w, i) => (
                <div key={i} className="text-[11px]">{w}</div>
              ))}
            </div>
          )}

          {/* 丢弃候选 */}
          {diagnostics.dropped_candidates.length > 0 && (
            <div className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] p-2">
              <div className="font-medium text-[var(--color-text)]">丢弃的候选</div>
              {diagnostics.dropped_candidates.map((d, i) => (
                <div key={i} className="text-[11px]">{d.reason}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function ChatView() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    if (!input.trim() || loading) return
    const question = input.trim()
    setInput('')
    setMessages(prev => [...prev, { role: 'user', content: question }])
    setLoading(true)

    try {
      const data = await apiPost<{
        answer?: string
        sources?: Message['sources']
        diagnostics?: Diagnostics
      }>('/api/chat/ask', { question })
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: data.answer || '未找到相关信息',
        sources: data.sources,
        diagnostics: data.diagnostics,
      }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: '请求失败，请重试' }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-xl font-bold mb-4">智能问答</h2>

      <div className="flex-1 overflow-auto space-y-4 mb-4">
        {messages.length === 0 && (
          <div className="text-[var(--color-text-muted)] text-center mt-20">
            输入问题，从知识库中获取答案
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] p-3 rounded-lg ${
              msg.role === 'user'
                ? 'bg-[var(--color-primary)] text-white'
                : 'bg-[var(--color-surface)] border border-[var(--color-border)]'
            }`}>
              <div className="whitespace-pre-wrap text-sm">{msg.content}</div>
              {msg.role === 'assistant' && (
                <>
                  {/* 引用来源 */}
                  {msg.sources && msg.sources.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-[var(--color-border)]">
                      <p className="text-xs text-[var(--color-text-muted)] mb-1">引用来源：</p>
                      {msg.sources.map((s, j) => (
                        <div key={j} className="mb-1 rounded border border-[var(--color-border)] bg-[var(--color-primary-soft)] px-2 py-1">
                          <div className="text-xs text-[var(--color-primary)]">{s.title || s.knowledge_id}</div>
                          {s.snippet && <div className="mt-0.5 text-xs text-[var(--color-text-muted)] line-clamp-2">{s.snippet}</div>}
                        </div>
                      ))}
                    </div>
                  )}
                  {/* 检索诊断面板 */}
                  {msg.diagnostics && <DiagnosticsPanel diagnostics={msg.diagnostics} />}
                </>
              )}
            </div>
          </div>
        ))}
        {loading && <div className="text-[var(--color-text-muted)] text-sm">思考中...</div>}
        <div ref={bottomRef} />
      </div>

      <div className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSend()}
          placeholder="输入你的问题..."
          className="flex-1 px-4 py-2 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm"
          disabled={loading}
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim()}
          className="px-6 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm disabled:opacity-50"
        >
          发送
        </button>
      </div>
    </div>
  )
}
