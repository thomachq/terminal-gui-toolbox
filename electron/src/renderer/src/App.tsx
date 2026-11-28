import { useCallback, useEffect, useState } from 'react'
import { CircleDot } from 'lucide-react'
import { api, getConfig, initConfig, type Session } from './api'
import { Sidebar, type Page } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { AssistantDock } from './components/AssistantDock'
import { LogDock } from './components/LogDock'
import { StatusBar } from './components/StatusBar'
import { ConnectionsPage } from './pages/ConnectionsPage'
import { TerminalPage } from './pages/TerminalPage'
import { FeaturePage } from './pages/FeaturePage'
import { HostsPage } from './pages/HostsPage'
import { ScriptsPage } from './pages/ScriptsPage'
import { CronPage } from './pages/CronPage'
import { EntityPage } from './pages/EntityPage'
import { LIGHT, DARK, applyTheme } from './theme'

export default function App() {
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [page, setPage] = useState<Page>('主机管理')
  const [sessions, setSessions] = useState<Session[]>([])
  const [active, setActive] = useState('')
  const [dark, setDark] = useState(false)
  const [assistantOpen, setAssistantOpen] = useState(true)
  const [logCollapsed, setLogCollapsed] = useState(true)

  const toggleTheme = () => {
    const next = !dark
    setDark(next); applyTheme(next ? DARK : LIGHT)
    localStorage.setItem('ttb-theme', next ? 'dark' : 'light')
  }
  useEffect(() => {
    const d = localStorage.getItem('ttb-theme') === 'dark'
    setDark(d); applyTheme(d ? DARK : LIGHT)
  }, [])

  const reload = useCallback(async () => {
    try {
      const r = await api.sessions()
      setSessions(r.sessions || [])
      setActive((old) => old && (r.sessions || []).some((s) => s.id === old)
        ? old : (r.sessions?.[0]?.id || ''))
    } catch { /* 桥掉了就留着上次列表 */ }
  }, [])

  useEffect(() => {
    initConfig().then((c) => {
      if (!c.ok) { setError(c.error || 'Python 桥启动失败'); return }
      setReady(true); reload()
    }).catch((e) => setError(String(e)))
  }, [reload])

  useEffect(() => {
    if (!ready) return
    const t = setInterval(reload, 5000)
    return () => clearInterval(t)
  }, [ready, reload])

  const activeSession = sessions.find((s) => s.id === active)
  const label = activeSession?.device?.hostname || activeSession?.note || active || '未选择设备'

  const content = () => {
    switch (page) {
      case '连接': return <ConnectionsPage sessions={sessions} activeId={active}
        onActive={setActive} onConnect={() => {}} reload={reload} goTerminal={() => setPage('终端')} />
      case '终端': return active ? <TerminalPage sid={active} label={label} /> : (
        <div className="h-full flex items-center justify-center text-sm" style={{ color: 'var(--text-mute)' }}>
          先在「连接」页连一台设备
        </div>)
      case '主机管理': return <HostsPage sessions={sessions} onConnect={() => {}} reloadSessions={reload} />
      case '脚本库': return <ScriptsPage sessions={sessions} />
      case '计划任务': return <CronPage sid={active} sessions={sessions} />
      case '凭证管理': return <EntityPage entity="creds" title="凭证管理"
        fields={[{ key: 'id', label: 'ID' }, { key: 'name', label: '名称' },
          { key: 'kind', label: '类型', options: ['password', 'key'] },
          { key: 'user', label: '用户' }, { key: 'secret', label: '密钥/口令', type: 'password' },
          { key: 'note', label: '备注' }]} />
      case '代理池': return <EntityPage entity="proxies" title="代理池"
        fields={[{ key: 'id', label: 'ID' }, { key: 'name', label: '名称' },
          { key: 'kind', label: '类型', options: ['socks5', 'http', 'ssh'] },
          { key: 'host', label: '主机' }, { key: 'port', label: '端口' },
          { key: 'user', label: '用户' }, { key: 'cred_id', label: '凭证 ID' },
          { key: 'parent_id', label: '上级代理' }, { key: 'note', label: '备注' }]} />
      default: return <FeaturePage name={page} sid={active} />
    }
  }

  if (!ready) return (
    <div className="h-screen flex flex-col items-center justify-center gap-4" style={{ color: 'var(--text-dim)' }}>
      {error ? (<>
        <CircleDot size={32} style={{ color: 'var(--err)' }} />
        <div className="text-sm" style={{ color: 'var(--err)' }}>{error}</div>
        <div className="text-xs" style={{ color: 'var(--text-mute)' }}>检查 python 是否可用，重启程序</div>
      </>) : (<>
        <CircleDot size={32} className="animate-pulse" style={{ color: 'var(--brand)' }} />
        <div className="text-sm">正在启动 Python 内核…</div>
        <div className="text-xs" style={{ color: 'var(--text-mute)' }}>端口 {getConfig().port || '…'}</div>
      </>)}
    </div>
  )

  return (
    <div className="h-screen flex flex-col" style={{ background: 'var(--page)' }}>
      <TopBar sessions={sessions} active={active} onActive={setActive}
        assistantOpen={assistantOpen} onAssistant={() => setAssistantOpen(!assistantOpen)} />
      <div className="flex-1 min-h-0 flex">
        <Sidebar page={page} onPage={setPage} sessions={sessions.length}
          activeLabel={label} dark={dark} onTheme={toggleTheme} />
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="flex-1 min-h-0 overflow-auto p-5">{content()}</div>
          <LogDock collapsed={logCollapsed} onToggle={() => setLogCollapsed(!logCollapsed)} />
        </div>
        {assistantOpen && <AssistantDock />}
      </div>
      <StatusBar />
    </div>
  )
}
