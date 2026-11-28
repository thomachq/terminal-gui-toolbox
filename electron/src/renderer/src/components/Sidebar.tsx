import { Cable, CircleDot, ExternalLink, LayoutGrid, Settings2, TerminalSquare } from 'lucide-react'

export const ALL_PAGES = [
  '连接', '终端', '主机管理', '代理池', '凭证管理', '脚本库', '网络工具',
  '远程管理', '系统信息', '文件管理', '路由表', '安全中心', 'Docker',
  'Web 服务', '数据库', '计划任务', '隧道服务', '智能体接入'
] as const
export type Page = typeof ALL_PAGES[number]

export const pageId = (p: Page) => p === '连接' ? 'connections' : p === '终端' ? 'terminal' : p

type Props = {
  page: Page
  onPage: (p: Page) => void
  sessions: number
  activeLabel: string
}

export function Sidebar({ page, onPage, sessions, activeLabel }: Props) {
  const nav = [
    { id: '连接' as Page, label: '连接', icon: Cable },
    { id: '终端' as Page, label: '终端', icon: TerminalSquare }
  ]
  return (
    <aside className="w-56 shrink-0 h-full flex flex-col border-r border-slate-800/80 bg-[#0e1420]">
      <div className="h-16 flex items-center gap-3 px-5 border-b border-slate-800/80">
        <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-violet-900/30">
          <CircleDot size={20} className="text-white" />
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-slate-100">Terminal Toolbox</div>
          <div className="text-[10px] tracking-wider text-violet-400">ELECTRON · PHASE 1</div>
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        <div className="px-3 pb-2 text-[10px] uppercase tracking-widest text-slate-500">工作台</div>
        {nav.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => onPage(id)}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition-colors ${page === id
              ? 'bg-violet-500/15 text-violet-300 ring-1 ring-violet-500/30'
              : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'}`}>
            <Icon size={17} /> {label}
            {id === '终端' && sessions > 0 && (
              <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded-full bg-violet-500/20 text-violet-300">
                {sessions}
              </span>
            )}
          </button>
        ))}
        <div className="px-3 pt-5 pb-2 text-[10px] uppercase tracking-widest text-slate-500">
          全部功能（接入中）
        </div>
        {ALL_PAGES.filter((p) => p !== '连接' && p !== '终端').map((label) => (
          <button key={label} onClick={() => onPage(label)}
            className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left text-xs transition-colors ${page === label
              ? 'bg-violet-500/15 text-violet-300 ring-1 ring-violet-500/30'
              : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'}`}>
            <LayoutGrid size={14} /> {label}
            <span className="ml-auto text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500/80">接入中</span>
          </button>
        ))}
      </nav>

      <div className="px-3 py-4 border-t border-slate-800/80 space-y-1">
        <button onClick={() => window.ttb.openClassic().catch(() => {})}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-xs text-slate-400 hover:bg-slate-800/60 hover:text-violet-300"
          title="打开完整功能的经典版（Tkinter）">
          <ExternalLink size={14} /> 打开经典版（全功能）
        </button>
        <div className="flex items-center gap-2 px-3 py-2 text-xs text-slate-500">
          <Settings2 size={14} />
          <span className="truncate">{activeLabel || '未选择设备'}</span>
        </div>
        <div className="px-3 text-[10px] text-slate-600">v1.0.40 · 内核复用 Python</div>
      </div>
    </aside>
  )
}
