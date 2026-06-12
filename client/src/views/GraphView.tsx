import { useEffect, useRef, useState } from 'react'
import { apiGet } from '../api'
import PageHeader from '../components/PageHeader'

interface GraphNode {
  id: string
  label: string
  type: string
  group?: number
}

interface GraphEdge {
  source: string
  target: string
  label?: string
}

interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export default function GraphView() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [data, setData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [hoverNode, setHoverNode] = useState<string | null>(null)

  useEffect(() => {
    apiGet<GraphData>('/api/graph/visualize?limit=100')
      .then(setData)
      .catch(err => setError(err instanceof Error ? err.message : '加载图谱失败'))
      .finally(() => setLoading(false))
  }, [])

  // Simple canvas renderer for graph visualization
  useEffect(() => {
    if (!data || !canvasRef.current || data.nodes.length === 0) return

    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const rect = canvas.parentElement?.getBoundingClientRect()
    canvas.width = rect?.width || 800
    canvas.height = rect?.height || 600

    const W = canvas.width
    const H = canvas.height
    const cx = W / 2
    const cy = H / 2
    const R = Math.min(W, H) * 0.35

    // Place nodes in a circle
    const positions = new Map<string, { x: number; y: number }>()
    data.nodes.forEach((node, i) => {
      const angle = (2 * Math.PI * i) / data.nodes.length
      positions.set(node.id, {
        x: cx + R * Math.cos(angle),
        y: cy + R * Math.sin(angle),
      })
    })

    // Clear
    ctx.clearRect(0, 0, W, H)

    // Draw edges
    ctx.strokeStyle = 'rgba(31, 74, 72, 0.15)'
    ctx.lineWidth = 1
    for (const edge of data.edges) {
      const s = positions.get(edge.source)
      const t = positions.get(edge.target)
      if (!s || !t) continue
      ctx.beginPath()
      ctx.moveTo(s.x, s.y)
      ctx.lineTo(t.x, t.y)
      ctx.stroke()
    }

    // Draw nodes
    for (const node of data.nodes) {
      const pos = positions.get(node.id)
      if (!pos) continue

      const isHover = hoverNode === node.id
      const isConnected = hoverNode
        ? data.edges.some(e =>
            (e.source === hoverNode && e.target === node.id) ||
            (e.target === hoverNode && e.source === node.id)
          )
        : true

      const alpha = isHover ? 1 : (hoverNode && !isConnected) ? 0.2 : 0.8
      const radius = isHover ? 8 : 5

      ctx.beginPath()
      ctx.arc(pos.x, pos.y, radius, 0, Math.PI * 2)
      ctx.fillStyle = `rgba(31, 74, 72, ${alpha})`
      ctx.fill()

      if (isHover) {
        ctx.fillStyle = 'rgba(31, 74, 72, 0.9)'
        ctx.font = '12px sans-serif'
        ctx.textAlign = 'center'
        ctx.fillText(node.label, pos.x, pos.y - 14)
      }
    }
  }, [data, hoverNode])

  // Mouse hover detection
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !data || data.nodes.length === 0) return

    const positions = new Map<string, { x: number; y: number }>()
    const W = canvas.width
    const H = canvas.height
    const cx = W / 2
    const cy = H / 2
    const R = Math.min(W, H) * 0.35
    data.nodes.forEach((node, i) => {
      const angle = (2 * Math.PI * i) / data.nodes.length
      positions.set(node.id, { x: cx + R * Math.cos(angle), y: cy + R * Math.sin(angle) })
    })

    const handleMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      let found: string | null = null
      for (const [id, pos] of positions) {
        if (Math.hypot(mx - pos.x, my - pos.y) < 10) { found = id; break }
      }
      setHoverNode(found)
    }
    canvas.addEventListener('mousemove', handleMove)
    return () => canvas.removeEventListener('mousemove', handleMove)
  }, [data])

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="知识图谱"
        subtitle={data ? `${data.nodes.length} 节点 · ${data.edges.length} 边` : '加载中...'}
      />

      {loading ? (
        <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">加载图谱数据...</div>
      ) : error ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <div className="text-[var(--color-text-muted)] mb-2">图谱数据不可用</div>
            <div className="text-xs text-[var(--color-text-muted)]">{error}</div>
          </div>
        </div>
      ) : !data || data.nodes.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
          暂无图谱数据。导入文档后自动构建。
        </div>
      ) : (
        <div className="flex-1 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg overflow-hidden">
          <canvas ref={canvasRef} className="w-full h-full" />
        </div>
      )}

      {/* Hover info */}
      {hoverNode && data && (
        <div className="mt-2 p-2 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm">
          {(() => {
            const node = data.nodes.find(n => n.id === hoverNode)
            const edges = data.edges.filter(e => e.source === hoverNode || e.target === hoverNode)
            return (
              <>
                <span className="font-medium">{node?.label || hoverNode}</span>
                <span className="text-[var(--color-text-muted)] ml-2">
                  ({node?.type}) · {edges.length} 个连接
                </span>
              </>
            )
          })()}
        </div>
      )}
    </div>
  )
}
