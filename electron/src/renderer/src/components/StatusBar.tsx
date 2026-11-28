import { Activity, Clock, Cpu, HardDrive, Wifi } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'

export function StatusBar() {
  const [info, setInfo] = useState<Record<string, unknown> | null>(null)
  useEffect(() => {
    let alive = true
    const load = async () => {
      try { const r = await api.sysinfo(); if (alive) setInfo(r) } catch { /* 离线就留空 */ }
    }
    load()
    const t = setInterval(load, 10000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  const cpu = info?.cpu || info?.cpu_usage
  const mem = info?.memory
  const fmtMem = (m: unknown) => {
    if (!m || typeof m !== 'object') return '—'
    const x = m as Record<string, number>
    return x.used != null ? `${(x.used / 1e9).toFixed(2)} / ${(x.total / 1e9).toFixed(2)} GB` : '—'
  }

  return (
    <div className="shrink-0 h-8 flex items-center gap-5 px-4 text-[11px]"
      style={{ background: 'var(--card)', borderTop: '1px solid var(--border)', color: 'var(--text-dim)' }}>
      <span className="flex items-center gap-1.5"><Activity size={12} /> fnOS</span>
      <span className="flex items-center gap-1.5"><Cpu size={12} /> CPU {cpu != null ? `${cpu}%` : '—'}</span>
      <span className="flex items-center gap-1.5"><HardDrive size={12} /> 内存 {fmtMem(mem)}</span>
      <span className="flex items-center gap-1.5"><Wifi size={12} /> 内核就绪</span>
      <span className="ml-auto flex items-center gap-1.5"><Clock size={12} /> 7 days</span>
      <span style={{ color: 'var(--brand)' }}>● 已登录 · 内核在线</span>
    </div>
  )
}
