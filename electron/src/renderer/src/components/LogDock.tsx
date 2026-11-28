import { useEffect, useState } from 'react'
import { ChevronDown, ChevronUp, Download, RefreshCw } from 'lucide-react'
import { api } from '../api'

type Ev = Record<string, unknown> & {
  id?: string; ts?: number; event?: string; status?: string
  detail?: string; device?: string; actor?: string
}

type Props = { collapsed: boolean; onToggle: () => void }

export function LogDock({ collapsed, onToggle }: Props) {
  const [rows, setRows] = useState<Ev[]>([])
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState(10)

  const load = async () => {
    try { const r = await api.storeList<Ev>('audit'); setRows((r.audit as Ev[]) || []) }
    catch { setRows([]) }
  }
  useEffect(() => { load() }, [])

  const filtered = rows.filter((r) => !q.trim() ||
    [r.event, r.detail, r.device, r.actor].some((v) => String(v || '').toLowerCase().includes(q.toLowerCase())))

  const exportLog = async () => {
    const text = filtered.map((r) => JSON.stringify(r)).join('\n')
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `ttb-audit-${Date.now()}.jsonl`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <div className="shrink-0" style={{ background: 'var(--card)', borderTop: '1px solid var(--border)' }}>
      <div className="flex items-center gap-3 px-4 py-2">
        <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>日志</span>
        <span className="text-[11px]" style={{ color: 'var(--text-mute)' }}>
          今天命令审计 · 键盘鼠标事件 · 窗口和后台程序切换
        </span>
        <div className="ml-auto flex gap-2">
          <button className="tt-btn tt-btn-ghost !py-1 !text-xs" onClick={load}><RefreshCw size={12} /> 刷新</button>
          <button className="tt-btn tt-btn-ghost !py-1 !text-xs" onClick={exportLog}><Download size={12} /> 导出日志</button>
          <button className="tt-btn tt-btn-ghost !py-1 !px-2" onClick={onToggle}>
            {collapsed ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
        </div>
      </div>
      {!collapsed && (
        <>
          <div className="flex items-center gap-3 px-4 py-2" style={{ borderTop: '1px solid var(--border)' }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索日志…"
              className="tt-input !w-56" />
            <span className="text-[11px]" style={{ color: 'var(--text-mute)' }}>
              共 {filtered.length} 条
            </span>
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}
              className="tt-input !py-1 !text-xs">
              {[10, 50, 100, 500].map((n) => <option key={n} value={n}>{n} 条/页</option>)}
            </select>
          </div>
          <div className="overflow-auto" style={{ maxHeight: 160 }}>
            <table className="w-full text-xs">
              <thead className="sticky top-0" style={{ background: 'var(--alt)' }}>
                <tr>
                  {['事件标题', '事件名', '分类', '状态', '设备', '时间'].map((h) => (
                    <th key={h} className="text-left font-medium px-4 py-1.5"
                      style={{ color: 'var(--text-dim)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.slice(0, limit).map((r, i) => (
                  <tr key={String(r.id || i)} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td className="px-4 py-1.5" style={{ color: 'var(--text)' }}>{String(r.detail || '').slice(0, 60)}</td>
                    <td className="px-4 py-1.5" style={{ color: 'var(--text-dim)' }}>{String(r.event || '')}</td>
                    <td className="px-4 py-1.5" style={{ color: 'var(--text-dim)' }}>AI</td>
                    <td className="px-4 py-1.5" style={{ color: r.status === 'ok' ? 'var(--ok)' : 'var(--err)' }}>{String(r.status || '')}</td>
                    <td className="px-4 py-1.5 truncate max-w-[180px]" style={{ color: 'var(--text-dim)' }}>{String(r.device || '')}</td>
                    <td className="px-4 py-1.5" style={{ color: 'var(--text-mute)' }}>
                      {r.ts ? new Date(Number(r.ts) * 1000).toLocaleString('zh-CN') : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
