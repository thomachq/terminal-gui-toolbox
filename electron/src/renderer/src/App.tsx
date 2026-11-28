import { useCallback, useEffect, useState } from 'react'
import { CircleDot } from 'lucide-react'
import { api, getConfig, initConfig, type Session } from './api'
import { Sidebar, type Page } from './components/Sidebar'
import { ConnectionsPage } from './pages/ConnectionsPage'
import { TerminalPage } from './pages/TerminalPage'
import { PlaceholderPage } from './pages/PlaceholderPage'

export default function App() {
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [page, setPage] = useState<Page>('连接')
  const [sessions, setSessions] = useState<Session[]>([])
  const [active, setActive] = useState('')

  const reload = useCallback(async () => {
    try {
      const r = await api.sessions()
      setSessions(r.sessions || [])
      setActive((old) => (old && (r.sessions || []).some((s) => s.id === old))
        ? old : (r.sessions?.[0]?.id || ''))
    } catch { /* 桥掉了就留着上次列表，不把界面清空 */ }
  }, [])

  useEffect(() => {
    initConfig().then((c) => {
      if (!c.ok) { setError(c.error || 'Python 桥启动失败'); return }
      setReady(true)
      reload()
    }).catch((e) => setError(String(e)))
  }, [reload])

  useEffect(() => {
    if (!ready) return
    const t = setInterval(reload, 5000)
    return () => clearInterval(t)
  }, [ready, reload])

  const activeSession = sessions.find((s) => s.id === active)
  const label = (activeSession?.device?.hostname || activeSession?.note || active || '未选择设备')

  if (!ready) {
    return (
      <div className="h-screen flex flex-col items-center justify-center gap-4 text-slate-400">
        {error ? (
          <>
            <CircleDot size={32} className="text-red-400" />
            <div className="text-sm text-red-400">{error}</div>
            <div className="text-xs text-slate-600">检查 python 是否可用，然后重启程序</div>
          </>
        ) : (
          <>
            <CircleDot size={32} className="animate-pulse text-violet-400" />
            <div className="text-sm">正在启动 Python 内核…</div>
            <div className="text-xs text-slate-600">端口 {getConfig().port || '…'}</div>
          </>
        )}
      </div>
    )
  }

  return (
    <div className="h-screen flex bg-[#0b0f19]">
      <Sidebar page={page} onPage={setPage} sessions={sessions.length} activeLabel={label} />
      <main className="flex-1 min-w-0 flex flex-col">
        <header className="h-14 shrink-0 flex items-center gap-3 px-6 border-b border-slate-800/80 bg-[#0e1420]">
          <span className="text-sm font-medium text-slate-200">
            {page === '连接' ? '连接设备' : page}
          </span>
          <span className="ml-auto text-[11px] text-slate-500">
            内核 {getConfig().port} · {sessions.length} 台在线
          </span>
        </header>
        <div className="flex-1 min-h-0">
          {page === '连接' ? (
            <ConnectionsPage sessions={sessions} activeId={active}
              onActive={setActive} onConnect={() => {}} reload={reload}
              goTerminal={() => setPage('终端')} />
          ) : page === '终端' ? (
            active ? <TerminalPage sid={active} label={label} /> : (
              <div className="h-full flex items-center justify-center text-sm text-slate-500">
                先在「连接」页连一台设备
              </div>
            )
          ) : (
            <PlaceholderPage name={page} sid={active} />
          )}
        </div>
      </main>
    </div>
  )
}
