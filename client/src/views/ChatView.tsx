import React, { useState, useRef, useEffect } from 'react'
import { apiPost } from '../api'

interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: { title: string; knowledge_id: string; block_id?: string; snippet?: string; score?: number }[]
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
      const data = await apiPost<{ answer?: string; sources?: Message['sources'] }>('/api/chat/ask', { question })
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: data.answer || '未找到相关信息',
        sources: data.sources,
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
