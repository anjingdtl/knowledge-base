import React from 'react'

export default function SettingsView() {
  return (
    <div>
      <h2 className="text-xl font-bold mb-4">设置</h2>
      <div className="space-y-4 max-w-lg">
        <div>
          <label className="block text-sm text-[var(--color-text-muted)] mb-1">LLM 模型</label>
          <input className="w-full px-3 py-2 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm" />
        </div>
        <div>
          <label className="block text-sm text-[var(--color-text-muted)] mb-1">API 地址</label>
          <input className="w-full px-3 py-2 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm" />
        </div>
        <div>
          <label className="block text-sm text-[var(--color-text-muted)] mb-1">Embedding 模型</label>
          <input className="w-full px-3 py-2 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm" />
        </div>
        <button className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg text-sm">
          保存设置
        </button>
      </div>
    </div>
  )
}
