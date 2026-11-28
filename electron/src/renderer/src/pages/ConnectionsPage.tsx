import { useEffect, useState } from 'react'
import { Link2, Loader2, Plus, Unplug } from 'lucide-react'
import { api, type Session } from '../api'

type Props = {
  sessions: Session[]
  activeId: string
  onActive: (id: string) => void
  onConnect: () => void
  reload: () => void
  goTerminal: () => void
}

const initial = {
  protocol: 'ssh', host: '', port: '22', user: '', auth: 'password',
  password: '', key_path: '', key_passphrase: ''
}

export function ConnectionsPage({ sessions, activeId, onActive, onConnect, reload, goTerminal }: Props) {
  const [f, setF] = useState({ ...initial })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [hosts, setHosts] = useState<unknown[]>([])

  useEffect(() => {
    api.hosts().then((r) => setHosts(r.hosts || [])).catch(() => setHosts([]))
  }, [])

  const set = (k: string, v: string) => setF((old) => ({ ...old, [k]: v }))

  const connect = async () => {
    setErr(''); setBusy(true)
    try {
      if (f.protocol === 'ssh' && !f.host) throw new Error('请填主机地址')
      const spec: Record<string, unknown> = {
        protocol: f.protocol,
        host: f.host,
        port: Number(f.port || (f.protocol === 'ssh' ? 22 : 0)),
        user: f.user,
        auth: f.auth,
        password: f.password,
        key_path: f.key_path,
        key_passphrase: f.key_passphrase,
        note: 'Electron 外壳'
      }
      await api.connect(spec)
      setF((old) => ({ ...old, password: '', key_passphrase: '' }))
      onConnect(); reload(); goTerminal()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const disconnect = async (id: string) => {
    try { await api.close(id); reload() } catch (e) { setErr(String(e)) }
  }

  const label = (s: Session) =>
    s.device?.hostname || s.note || s.id.slice(0, 8)

  const field = (key: string, labelText: string, type = 'text', placeholder = '') => (
    <label className="block">
      <span className="text-xs text-slate-400">{labelText}</span>
      <input type={type} value={String((f as Record<string, string>)[key] || '')}
        placeholder={placeholder} onChange={(e) => set(key, e.target.value)}
        className="mt-1.5 w-full rounded-xl bg-[#0b1220] border border-slate-700/70 px-3 py-2 text-sm text-slate-200 outline-none focus:border-violet-500/70 focus:ring-2 focus:ring-violet-500/20" />
    </label>
  )

  return (
    <div className="h-full overflow-auto p-7 space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">连接设备</h1>
        <p className="text-sm text-slate-500 mt-1">
          Phase 1 支持 SSH 与本机；其余协议在 Tkinter 版照旧可用。
        </p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <section className="rounded-2xl border border-slate-800 bg-[#111827] p-6 shadow-xl shadow-black/20">
          <div className="flex items-center gap-2 mb-5 text-sm font-medium text-slate-200">
            <Plus size={16} className="text-violet-400" /> 新建连接
          </div>
          <div className="space-y-4">
            <label className="block">
              <span className="text-xs text-slate-400">协议</span>
              <select value={f.protocol} onChange={(e) => {
                const p = e.target.value
                setF((old) => ({ ...old, protocol: p, port: p === 'ssh' ? '22' : '0' }))
              }} className="mt-1.5 w-full rounded-xl bg-[#0b1220] border border-slate-700/70 px-3 py-2 text-sm text-slate-200 outline-none focus:border-violet-500/70">
                <option value="ssh">SSH</option>
                <option value="local">本机</option>
              </select>
            </label>
            {f.protocol === 'ssh' && (
              <>
                <div className="grid grid-cols-3 gap-3">
                  {field('host', '主机', 'text', '192.168.3.12')}
                  {field('port', '端口', 'text', '22')}
                  {field('user', '用户', 'text', 'root')}
                </div>
                <label className="block">
                  <span className="text-xs text-slate-400">认证</span>
                  <select value={f.auth} onChange={(e) => set('auth', e.target.value)}
                    className="mt-1.5 w-full rounded-xl bg-[#0b1220] border border-slate-700/70 px-3 py-2 text-sm text-slate-200 outline-none focus:border-violet-500/70">
                    <option value="password">密码</option>
                    <option value="key">私钥</option>
                  </select>
                </label>
                {f.auth === 'password'
                  ? field('password', '密码', 'password')
                  : <>{field('key_path', '私钥路径')}{field('key_passphrase', '私钥口令', 'password')}</>}
              </>
            )}
            {err && <div className="rounded-xl bg-red-500/10 border border-red-500/30 px-3 py-2 text-xs text-red-300">{err}</div>}
            <button onClick={connect} disabled={busy}
              className="w-full rounded-xl bg-violet-600 hover:bg-violet-500 disabled:opacity-50 py-2.5 text-sm font-medium text-white flex items-center justify-center gap-2 shadow-lg shadow-violet-900/30">
              {busy ? <Loader2 size={15} className="animate-spin" /> : <Link2 size={15} />}
              {busy ? '连接中…' : '连接'}
            </button>
            <div className="text-[11px] text-slate-600">
              已保存档案：{hosts.length} 条（读写走既有 profiles.dat，未在此页展开）
            </div>
          </div>
        </section>

        <section className="rounded-2xl border border-slate-800 bg-[#111827] p-6 shadow-xl shadow-black/20">
          <div className="flex items-center gap-2 mb-5 text-sm font-medium text-slate-200">
            <Link2 size={16} className="text-violet-400" /> 已连接会话
            <span className="ml-auto text-xs text-slate-500">{sessions.length} 台</span>
          </div>
          {sessions.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-700/70 py-12 text-center text-sm text-slate-500">
              还没有会话，左侧连一台试试
            </div>
          ) : (
            <div className="space-y-2">
              {sessions.map((s) => (
                <div key={s.id}
                  onClick={() => onActive(s.id)}
                  className={`rounded-xl border px-4 py-3 cursor-pointer transition-colors ${s.id === activeId
                    ? 'border-violet-500/50 bg-violet-500/10'
                    : 'border-slate-700/60 bg-[#0b1220] hover:border-slate-600'}`}>
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-emerald-400 shadow shadow-emerald-500/50" />
                    <span className="text-sm text-slate-200">{label(s)}</span>
                    <span className="ml-auto text-[10px] text-slate-500">{s.protocol || 'ssh'}</span>
                    <button onClick={(e) => { e.stopPropagation(); disconnect(s.id) }}
                      className="rounded-lg p-1.5 text-slate-500 hover:text-red-400 hover:bg-red-500/10"
                      title="断开"><Unplug size={14} /></button>
                  </div>
                  {s.device?.os_name && (
                    <div className="mt-1 pl-4 text-[11px] text-slate-500 truncate">{s.device.os_name}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
