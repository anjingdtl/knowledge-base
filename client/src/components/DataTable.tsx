import type { ReactNode } from 'react'

interface Column<T> {
  key: string
  title: string
  render?: (row: T, index: number) => ReactNode
  className?: string
}

interface Props<T> {
  columns: Column<T>[]
  data: T[]
  loading?: boolean
  emptyText?: string
  rowKey?: (row: T, index: number) => string
  onRowClick?: (row: T, index: number) => void
  footer?: ReactNode
}

export default function DataTable<T>({
  columns,
  data,
  loading,
  emptyText = '暂无数据',
  rowKey,
  onRowClick,
  footer,
}: Props<T>) {
  if (loading) {
    return <div className="py-10 text-center text-[var(--color-text-muted)]">加载中...</div>
  }

  if (data.length === 0) {
    return <div className="py-10 text-center text-[var(--color-text-muted)]">{emptyText}</div>
  }

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg)]">
            {columns.map(col => (
              <th key={col.key} className={`px-4 py-2.5 text-left font-medium text-[var(--color-text-muted)] ${col.className || ''}`}>
                {col.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr
              key={rowKey ? rowKey(row, i) : i}
              onClick={onRowClick ? () => onRowClick(row, i) : undefined}
              className={`border-b border-[var(--color-border)] last:border-b-0 ${
                onRowClick ? 'cursor-pointer hover:bg-[var(--color-surface-hover)]' : ''
              }`}
            >
              {columns.map(col => (
                <td key={col.key} className={`px-4 py-3 ${col.className || ''}`}>
                  {col.render ? col.render(row, i) : String((row as Record<string, unknown>)[col.key] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {footer && <div className="border-t border-[var(--color-border)] px-4 py-3">{footer}</div>}
    </div>
  )
}

export type { Column }
