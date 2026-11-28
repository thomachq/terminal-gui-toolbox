import { useEffect, useState } from 'react'
import { Boxes, CircleCheck, Database, FileCode2, Globe2, Route, ShieldCheck, Wrench } from 'lucide-react'
import { api } from '../api'

const ICONS: Record<string, typeof Boxes> = {
  '主机管理': Globe2, '代理池': Wrench, '凭证管理': Wrench, '脚本库': FileCode2,
  '网络工具': Wrench, '远程管理': Globe2, '系统信息': Wrench, '文件管理': Database,
  '路由表': Route, '安全中心': ShieldCheck, 'Docker': Boxes, 'Web 服务': Globe2,
  '数据库': Database, '计划任务': FileCode2, '隧道服务': ShieldCheck,
  '智能体接入': Boxes
}

type Props = { name: string; sid: string }

/** 尚未做完完整交互的页：显示**真数据**（不伪造），标明与经典版对齐状态。 */
export function FeaturePage({ name, sid }: Props) {
  const [data, setData] = useState<string>('加载中…')
  const [err, setErr] = useState('')
  const [ms, setMs] = useState(0)

  useEffect(() => {
    let alive = true
    setData('加载中…'); setErr(''); setMs(0)
    const t0 = Date.now()
    const load = async () => {
      try {
        if (name === '系统信息') {
          const r = await api.sysinfo(sid || undefined); setData(JSON.stringify(r, null, 2))
        } else if (name === 'Docker') {
          const r = await api.docker('all', sid || undefined); setData(JSON.stringify(r, null, 2))
        } else if (name === '网络工具') {
          const r = await api.services(sid || undefined); setData(JSON.stringify(r, null, 2))
        } else {
          const cat = await api.coreCatalog()
          const prefix: Record<string, string> = {
            '主机管理': 'host.', '脚本库': 'scripts.', '路由表': 'routes.',
            '安全中心': 'sec.', 'Web 服务': 'web.', '数据库': 'db.',
            '计划任务': 'cron.', '证书': 'cert.'
          }
          const p = prefix[name] || ''
          const items = cat.items.filter((x) => x.name.startsWith(p))
          setData(p
            ? `本页已注册 ${items.length} 个 core 函数（与经典版同一份实现）：\n\n` +
              items.map((x) => `  ${x.name} — ${x.desc}`).join('\n')
            : `「${name}」与经典版共用 Python 内核；完整交互正在接入。\n\n` +
              `core 桥共 ${cat.count} 个函数已就绪，可在「智能体接入」页查看清单。`)
        }
        if (alive) setMs(Date.now() - t0)
      } catch (e) { if (alive) setErr(e instanceof Error ? e.message : String(e)) }
    }
    load()
    return () => { alive = false }
  }, [name, sid])

  const Icon = ICONS[name] || Boxes
  return (
    <div className="h-full overflow-auto p-7">
      <div className="flex items-center gap-3 mb-5">
        <div className="w-10 h-10 rounded-xl flex items-center justify-center"
          style={{ background: 'var(--brand-soft)' }}>
          <Icon size={20} style={{ color: 'var(--brand)' }} />
        </div>
        <div>
          <h1 className="text-xl font-semibold" style={{ color: 'var(--text)' }}>{name}</h1>
          <p className="text-xs" style={{ color: 'var(--text-mute)' }}>
            {sid ? `会话 ${sid.slice(0, 8)}` : '未选择会话'}{ms ? ` · ${ms} ms` : ''}
          </p>
        </div>
        <span className="ml-auto text-[11px] px-2.5 py-1 rounded-full"
          style={{ background: 'var(--brand-soft)', color: 'var(--brand-deep)' }}>
          与经典版共用内核
        </span>
      </div>
      {err ? (
        <div className="tt-card p-5 text-sm" style={{ color: 'var(--err)' }}>{err}</div>
      ) : (
        <pre className="tt-card p-5 text-xs whitespace-pre-wrap leading-relaxed"
          style={{ color: 'var(--text)' }}>{data}</pre>
      )}
      <div className="mt-4 flex items-center gap-2 text-xs" style={{ color: 'var(--text-dim)' }}>
        <CircleCheck size={14} style={{ color: 'var(--ok)' }} />
        数据来自真实 Python 内核；经典版功能一行未删
      </div>
    </div>
  )
}
