import { useMemo, useState } from 'react'
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
                <th key={c.key} className="text-left font-medium px-4 py-2.5 text-xs"
                  style={{ color: 'var(--text-dim)', borderBottom: '1px solid var(--border)', width: c.width }}>
                  {c.title}
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
