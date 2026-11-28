import { useEffect, useState } from 'react'
import { Boxes, CircleCheck, Database, FileCode2, Globe2, Route, ShieldCheck, Wrench } from 'lucide-react'
import { api } from '../api'

const ICONS: Record<string, typeof Boxes> = {
  '网络工具': Wrench, '远程管理': Globe2, '路由表': Route,
  '安全中心': ShieldCheck, 'Docker': Boxes, 'Web 服务': Globe2,
  '数据库': Database, '计划任务': FileCode2
}

type Props = { name: string; sid: string }

/** 尚未做完整交互的页：显示**真数据**（不伪造），并标出此页需要的 core 函数。 */
export function PlaceholderPage({ name, sid }: Props) {
  const [data, setData] = useState<string>('加载中…')
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        if (name === '系统信息') {
          const r = await api.sysinfo(sid || undefined)
          setData(JSON.stringify(r, null, 2))
        } else if (name === 'Docker') {
          const r = await api.docker('all', sid || undefined)
          setData(JSON.stringify(r, null, 2))
        } else if (name === '网络工具') {
          const r = await api.services(sid || undefined)
          setData(JSON.stringify(r, null, 2))
        } else {
          const cat = await api.coreCatalog()
          const prefix: Record<string, string> = {
            '路由表': 'routes.', '安全中心': 'sec.', 'Web 服务': 'web.',
            '数据库': 'db.', '计划任务': 'cron.'
          }
          const p = prefix[name] || ''
          const items = cat.items.filter((x) => x.name.startsWith(p))
          setData(p
            ? `本页已注册 ${items.length} 个 core 函数，等待接入界面：\n` +
              items.map((x) => `  ${x.name} — ${x.desc}`).join('\n')
            : `「${name}」的 core 能力已就绪，等待接入界面。`)
        }
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : String(e))
      }
    }
    load()
    return () => { alive = false }
  }, [name, sid])

  const Icon = ICONS[name] || Boxes
  return (
    <div className="h-full overflow-auto p-7">
      <div className="flex items-center gap-3 mb-5">
        <div className="w-10 h-10 rounded-xl bg-violet-500/15 flex items-center justify-center">
          <Icon size={20} className="text-violet-400" />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-slate-100">{name}</h1>
          <p className="text-xs text-slate-500">{sid ? `会话 ${sid.slice(0, 8)}` : '未选择会话'}</p>
        </div>
        <span className="ml-auto text-[11px] px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-400 ring-1 ring-amber-500/30">
          迁移中 · 核心能力已就绪
        </span>
      </div>
      {err ? (
        <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-5 text-sm text-red-300">{err}</div>
      ) : (
        <pre className="rounded-2xl border border-slate-800 bg-[#111827] p-5 text-xs text-slate-300 whitespace-pre-wrap leading-relaxed">
          {data}
        </pre>
      )}
      <div className="mt-4 flex items-center gap-2 text-xs text-slate-500">
        <CircleCheck size={14} className="text-emerald-500" />
        数据来自真实 Python 内核；经典版同功能一行未删
      </div>
    </div>
  )
}
