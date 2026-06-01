import React, { useState } from 'react'
import KnowledgeView from './views/KnowledgeView'
import ChatView from './views/ChatView'
import WikiView from './views/WikiView'
import GraphView from './views/GraphView'
import SettingsView from './views/SettingsView'

type Tab = 'knowledge' | 'chat' | 'wiki' | 'graph' | 'settings'

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: 'knowledge', label: '知识库', icon: '📚' },
  { key: 'chat', label: '智能问答', icon: '💬' },
  { key: 'wiki', label: 'Wiki', icon: '📖' },
  { key: 'graph', label: '知识图谱', icon: '🕸️' },
  { key: 'settings', label: '设置', icon: '⚙️' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('knowledge')

  const renderView = () => {
    switch (activeTab) {
      case 'knowledge': return <KnowledgeView />
      case 'chat': return <ChatView />
      case 'wiki': return <WikiView />
      case 'graph': return <GraphView />
      case 'settings': return <SettingsView />
    }
  }

  return (
    <div className="flex h-screen">
      {/* 侧边栏 */}
      <nav className="w-52 bg-[var(--color-surface)] border-r border-[var(--color-border)] flex flex-col p-3 gap-1">
        <h1 className="text-lg font-bold px-3 py-2 text-[var(--color-accent)]">
          ShineHe KB
        </h1>
        {TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors
              ${activeTab === tab.key
                ? 'bg-[var(--color-primary)] text-white'
                : 'text-[var(--color-text-muted)] hover:bg-[var(--color-border)]'
              }`}
          >
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </nav>

      {/* 主内容区 */}
      <main className="flex-1 overflow-auto p-6">
        {renderView()}
      </main>
    </div>
  )
}
