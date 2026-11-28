import {
  Anchor, Cable, CircleDot, Cloud, Database, FileCode2, FileText, Globe2,
  KeyRound, ListOrdered, Moon, Network, ScrollText, Server, ShieldCheck,
  SlidersHorizontal, Sun, TerminalSquare, Wrench, Bot
} from 'lucide-react'

export const ALL_PAGES = [
  '连接', '终端', '主机管理', '代理池', '凭证管理', '脚本库', '网络工具',
  '远程管理', '系统信息', '文件管理', '路由表', '安全中心', 'Docker',
  'Web 服务', '数据库', '计划任务', '隧道服务', '智能体接入'
] as const
export type Page = typeof ALL_PAGES[number]

const ICONS: Record<Page, typeof Cable> = {
  '连接': Cable, '终端': TerminalSquare, '主机管理': Server,
  '代理池': Network, '凭证管理': KeyRound, '脚本库': FileCode2,
  '网络工具': Wrench, '远程管理': Globe2, '系统信息': SlidersHorizontal,
  '文件管理': FileText, '路由表': ListOrdered, '安全中心': ShieldCheck,
  'Docker': Cloud, 'Web 服务': Globe2, '数据库': Database,
  '计划任务': ScrollText, '隧道服务': Anchor, '智能体接入': Bot
}

const GROUPS: { title: string; pages: Page[] }[] = [
  { title: '工作台', pages: ['连接', '终端'] },
  { title: '主机资源', pages: ['主机管理', '代理池', '凭证管理', '脚本库'] },
  { title: '远程工具', pages: ['远程管理', '系统信息', '文件管理', '路由表'] },
  { title: '系统管理', pages: ['安全中心', 'Docker', 'Web 服务', '数据库', '计划任务', '隧道服务', '智能体接入'] }
]

type Props = {
  page: Page; onPage: (p: Page) => void; sessions: number; activeLabel: string
  dark: boolean; onTheme: () => void
}

export function Sidebar({ page, onPage, sessions, activeLabel, dark, onTheme }: Props) {
  return (
    <aside className="w-[132px] shrink-0 h-full flex flex-col overflow-y-auto"
      style={{ background: 'var(--card)', borderRight: '1px solid var(--border)' }}>
      <div className="px-3 py-3 flex items-center gap-2" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0" style={{ background: 'var(--brand)' }}>
          <CircleDot size={16} className="text-white" />
        </div>
        <div className="leading-tight min-w-0">
          <div className="text-[11px] font-semibold truncate" style={{ color: 'var(--text)' }}>Terminal Toolbox</div>
          <div className="text-[9px]" style={{ color: 'var(--brand)' }}>v1.0.40</div>
        </div>
      </div>
      <nav className="flex-1 py-2">
        {GROUPS.map((g) => (
          <div key={g.title}>
            <div className="px-3 pt-3 pb-1 text-[9px] uppercase tracking-wider"
              style={{ color: 'var(--text-mute)' }}>{g.title}</div>
            {g.pages.map((id) => {
              const Icon = ICONS[id]
              return (
                <button key={id} onClick={() => onPage(id)}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-[11px] transition-colors"
                  style={page === id
                    ? { background: 'var(--brand-soft)', color: 'var(--brand-deep)', fontWeight: 600, borderLeft: '3px solid var(--brand)' }
                    : { color: 'var(--text-dim)' }}>
                  <Icon size={13} className="shrink-0" />
                  <span className="truncate">{id}</span>
                  {id === '终端' && sessions > 0 && (
                    <span className="ml-auto" style={{ color: 'var(--brand)' }}>{sessions}</span>
                  )}
                </button>
              )
            })}
          </div>
        ))}
        <div className="px-3 pt-3 pb-1 text-[9px] uppercase tracking-wider" style={{ color: 'var(--text-mute)' }}>日志</div>
        <div className="px-3 py-1.5 text-[11px]" style={{ color: 'var(--text-dim)' }}>今日日志</div>
      </nav>
      <div className="p-2 space-y-1" style={{ borderTop: '1px solid var(--border)' }}>
        <div className="flex gap-1">
          <button onClick={onTheme} className="tt-btn tt-btn-ghost flex-1 !py-1 !px-1 !text-[10px]">
            {dark ? <Sun size={11} /> : <Moon size={11} />} {dark ? '浅色' : '深色'}
          </button>
          <button onClick={() => window.ttb.openClassic().catch(() => {})}
            className="tt-btn tt-btn-ghost flex-1 !py-1 !px-1 !text-[10px]">经典版</button>
        </div>
      </div>
    </aside>
  )
}
