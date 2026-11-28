import { useEffect, useMemo, useRef, useState } from 'react'
import { Search } from 'lucide-react'

export type Column<T> = {
  key: string; title: string; width?: number
  render?: (row: T) => React.ReactNode
}

type Props<T> = {
  columns: Column<T>[]; rows: T[]; rowKey: (row: T) => string
  onRowClick?: (row: T) => void; selectedKey?: string; searchKeys?: string[]
  toolbar?: React.ReactNode; height?: number
}

export function DataTable<T extends Record<string, unknown>>({
  columns, rows, rowKey, onRowClick, selectedKey, searchKeys = [], toolbar, height = 520
}: Props<T>) {
  const [q, setQ] = useState('')
  const [widths, setWidths] = useState<Record<string, number>>({})
  const colDrag = useRef<{ key: string; start: number; initial: number } | null>(null)

  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!colDrag.current) return
      const d = e.clientX - colDrag.current.start
      const k = colDrag.current.key
      setWidths((w) => ({ ...w, [k]: Math.max(60, colDrag.current!.initial + d) }))
    }
    const up = () => { colDrag.current = null }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
  }, [])

  const filtered = useMemo(() => {
    if (!q.trim()) return rows
    const needle = q.trim().toLowerCase()
    return rows.filter((r) => searchKeys.some((k) =>
      String(r[k] ?? '').toLowerCase().includes(needle)))
  }, [q, rows, searchKeys])

  return (
    <div className="tt-card overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-2.5"
            style={{ color: 'var(--text-mute)' }} />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="搜索…" className="tt-input !pl-8 !w-56" />
        </div>
        <span className="text-xs" style={{ color: 'var(--text-mute)' }}>
          {filtered.length} / {rows.length} 条
        </span>
        <div className="ml-auto flex gap-2">{toolbar}</div>
      </div>
      <div className="overflow-auto" style={{ maxHeight: height }}>
        <table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
          <thead className="sticky top-0 z-10" style={{ background: 'var(--alt)' }}>
            <tr>
              {columns.map((c) => (
                <th key={c.key} className="text-left font-medium px-4 py-2.5 text-xs relative"
                  style={{ color: 'var(--text-dim)', borderBottom: '1px solid var(--border)',
                    width: widths[c.key] ?? c.width }}>
                  {c.title}
                  <span
                    onMouseDown={(e) => {
                      e.preventDefault(); e.stopPropagation()
                      colDrag.current = { key: c.key, start: e.clientX,
                        initial: widths[c.key] ?? c.width ?? 120 }
                    }}
                    className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-blue-400"
                    style={{ background: colDrag.current?.key === c.key ? 'var(--brand)' : 'transparent' }}
                    title="拖动调整列宽" />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((row) => {
              const key = rowKey(row)
              return (
                <tr key={key} onClick={() => onRowClick?.(row)}
                  className={onRowClick ? 'cursor-pointer' : ''}
                  style={{ background: key === selectedKey ? 'var(--brand-soft)' : 'transparent',
                    borderBottom: '1px solid var(--border)' }}>
                  {columns.map((c) => (
                    <td key={c.key} className="px-4 py-2.5 text-xs"
                      style={{ color: 'var(--text)' }}>
                      {c.render ? c.render(row) : String(row[c.key] ?? '')}
                    </td>
                  ))}
                </tr>
              )
            })}
            {filtered.length === 0 && (
              <tr><td colSpan={columns.length} className="text-center py-12 text-sm"
                style={{ color: 'var(--text-mute)' }}>没有匹配的记录</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
