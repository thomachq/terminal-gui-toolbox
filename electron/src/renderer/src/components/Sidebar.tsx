import {
  Anchor, Cable, CircleDot, Cloud, Database, FileCode2, FileText, Globe2,
  KeyRound, LayoutGrid, ListOrdered, Moon, Network, ScrollText, Server,
  ShieldCheck, SlidersHorizontal, Sun, TerminalSquare, Wrench
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
  '计划任务': ScrollText, '隧道服务': Anchor, '智能体接入': LayoutGrid
}

type Props = {
  page: Page; onPage: (p: Page) => void; sessions: number; activeLabel: string
  dark: boolean; onTheme: () => void
}

export function Sidebar({ page, onPage, sessions, activeLabel, dark, onTheme }: Props) {
  const nav = (['连接', '终端'] as Page[]).map((id) => ({ id, icon: ICONS[id] }))
  const rest = ALL_PAGES.slice(2)
  return (
    <aside className="w-56 shrink-0 h-full flex flex-col"
      style={{ background: 'var(--card)', borderRight: '1px solid var(--border)' }}>
      <div className="h-16 flex items-center gap-3 px-4"
        style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="w-9 h-9 rounded-xl flex items-center justify-center"
          style={{ background: 'var(--brand)' }}>
          <CircleDot size={20} className="text-white" />
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>Terminal Toolbox</div>
          <div className="text-[10px] tracking-wider" style={{ color: 'var(--brand)' }}>v1.0.40 · ELECTRON</div>
        </div>
      </div>

      <nav className="flex-1 overflow-auto px-3 py-3 space-y-0.5">
        <div className="px-2 pb-2 text-[10px] uppercase tracking-widest" style={{ color: 'var(--text-mute)' }}>工作台</div>
        {nav.map(({ id, icon: Icon }) => (
          <button key={id} onClick={() => onPage(id)}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors"
            style={page === id
              ? { background: 'var(--brand-soft)', color: 'var(--brand-deep)', fontWeight: 600 }
              : { color: 'var(--text-dim)' }}>
            <Icon size={16} /> {id}
            {id === '终端' && sessions > 0 && (
              <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded-full"
                style={{ background: 'var(--brand-soft2)', color: 'var(--brand-deep)' }}>{sessions}</span>
            )}
          </button>
        ))}
        <div className="px-2 pt-4 pb-2 text-[10px] uppercase tracking-widest" style={{ color: 'var(--text-mute)' }}>全部功能</div>
        {rest.map((id) => {
          const Icon = ICONS[id]
          return (
            <button key={id} onClick={() => onPage(id)}
              className="w-full flex items-center gap-3 px-3 py-[7px] rounded-lg text-left text-xs transition-colors"
              style={page === id
                ? { background: 'var(--brand-soft)', color: 'var(--brand-deep)', fontWeight: 600 }
                : { color: 'var(--text-dim)' }}>
              <Icon size={14} /> {id}
            </button>
          )
        })}
      </nav>

      <div className="p-3 space-y-1" style={{ borderTop: '1px solid var(--border)' }}>
        <div className="flex items-center gap-2 px-2 py-1.5 text-xs" style={{ color: 'var(--text-dim)' }}>
          <CircleDot size={13} />
          <span className="truncate">{activeLabel || '未选择设备'}</span>
        </div>
        <div className="flex gap-2">
          <button onClick={onTheme} className="tt-btn tt-btn-ghost flex-1 !py-1.5 !text-xs"
            title="切换主题（与经典版一致）">
            {dark ? <Sun size={13} /> : <Moon size={13} />} {dark ? '浅色' : '深色'}
          </button>
          <button onClick={() => window.ttb.openClassic().catch(() => {})}
            className="tt-btn tt-btn-ghost flex-1 !py-1.5 !text-xs" title="经典版全功能入口">
            <TerminalSquare size={13} /> 经典版
          </button>
        </div>
      </div>
    </aside>
  )
}
