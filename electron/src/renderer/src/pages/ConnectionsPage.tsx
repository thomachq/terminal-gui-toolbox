import { useEffect, useState } from 'react'
import { Cable, Link2, Loader2, Plus, Unplug } from 'lucide-react'
import { api, type Session } from '../api'

type Props = {
  sessions: Session[]; activeId: string; onActive: (id: string) => void
  onConnect: () => void; reload: () => void; goTerminal: () => void
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

  useEffect(() => { api.hosts().then((r) => setHosts(r.hosts || [])).catch(() => setHosts([])) }, [])
  const set = (k: string, v: string) => setF((old) => ({ ...old, [k]: v }))

  const connect = async () => {
    setErr(''); setBusy(true)
    try {
      if (f.protocol === 'ssh' && !f.host) throw new Error('请填主机地址')
      await api.connect({
        protocol: f.protocol, host: f.host,
        port: Number(f.port || (f.protocol === 'ssh' ? 22 : 0)),
        user: f.user, auth: f.auth, password: f.password,
        key_path: f.key_path, key_passphrase: f.key_passphrase, note: 'Electron 外壳'
      })
      setF((old) => ({ ...old, password: '', key_passphrase: '' }))
      onConnect(); reload(); goTerminal()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  const disconnect = async (id: string) => {
    try { await api.close(id); reload() } catch (e) { setErr(String(e)) }
  }
  const label = (s: Session) => s.device?.hostname || s.note || s.id.slice(0, 8)

  const field = (key: string, labelText: string, type = 'text', placeholder = '') => (
    <label className="block">
      <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{labelText}</span>
      <input type={type} value={String((f as Record<string, string>)[key] || '')}
        placeholder={placeholder} onChange={(e) => set(key, e.target.value)}
        className="tt-input mt-1.5 w-full" />
    </label>
  )

  return (
    <div className="h-full overflow-auto p-7 space-y-6">
      <div>
        <h1 className="text-xl font-semibold" style={{ color: 'var(--text)' }}>连接设备</h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-dim)' }}>
          与经典版共用同一内核和档案；Phase 1 支持 SSH / 本机，其余协议经典版照旧可用。
        </p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <section className="tt-card p-6">
          <div className="flex items-center gap-2 mb-5 text-sm font-medium" style={{ color: 'var(--text)' }}>
            <Plus size={16} style={{ color: 'var(--brand)' }} /> 新建连接
          </div>
          <div className="space-y-4">
            <label className="block">
              <span className="text-xs" style={{ color: 'var(--text-dim)' }}>协议</span>
              <select value={f.protocol} onChange={(e) => {
                const p = e.target.value
                setF((old) => ({ ...old, protocol: p, port: p === 'ssh' ? '22' : '0' }))
              }} className="tt-input mt-1.5 w-full">
                <option value="ssh">SSH</option><option value="local">本机</option>
              </select>
            </label>
            {f.protocol === 'ssh' && (<>
              <div className="grid grid-cols-3 gap-3">
                {field('host', '主机', 'text', '192.168.3.12')}
                {field('port', '端口', 'text', '22')}
                {field('user', '用户', 'text', 'root')}
              </div>
              <label className="block">
                <span className="text-xs" style={{ color: 'var(--text-dim)' }}>认证</span>
                <select value={f.auth} onChange={(e) => set('auth', e.target.value)}
                  className="tt-input mt-1.5 w-full">
                  <option value="password">密码</option><option value="key">私钥</option>
                </select>
              </label>
              {f.auth === 'password' ? field('password', '密码', 'password')
                : <>{field('key_path', '私钥路径')}{field('key_passphrase', '私钥口令', 'password')}</>}
            </>)}
            {err && <div className="rounded-xl px-3 py-2 text-xs"
              style={{ background: 'var(--err-soft)', color: 'var(--err)' }}>{err}</div>}
            <button onClick={connect} disabled={busy} className="tt-btn tt-btn-primary w-full">
              {busy ? <Loader2 size={15} className="animate-spin" /> : <Cable size={15} />}
              {busy ? '连接中…' : '连接'}
            </button>
            <div className="text-[11px]" style={{ color: 'var(--text-mute)' }}>
              已保存档案 {hosts.length} 条（读写走 profiles.dat，与经典版共用）
            </div>
          </div>
        </section>

        <section className="tt-card p-6">
          <div className="flex items-center gap-2 mb-5 text-sm font-medium" style={{ color: 'var(--text)' }}>
            <Link2 size={16} style={{ color: 'var(--brand)' }} /> 已连接会话
            <span className="ml-auto text-xs" style={{ color: 'var(--text-mute)' }}>{sessions.length} 台</span>
          </div>
          {sessions.length === 0 ? (
            <div className="rounded-xl border border-dashed py-12 text-center text-sm"
              style={{ borderColor: 'var(--border2)', color: 'var(--text-mute)' }}>
              还没有会话，左侧连一台试试
            </div>
          ) : (
            <div className="space-y-2">
              {sessions.map((s) => (
                <div key={s.id} onClick={() => onActive(s.id)}
                  className="rounded-xl border px-4 py-3 cursor-pointer transition-colors"
                  style={s.id === activeId
                    ? { background: 'var(--brand-soft)', borderColor: 'var(--brand)' }
                    : { background: 'var(--alt)', borderColor: 'var(--border)' }}>
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full" style={{ background: 'var(--ok)' }} />
                    <span className="text-sm" style={{ color: 'var(--text)' }}>{label(s)}</span>
                    <span className="ml-auto text-[10px]" style={{ color: 'var(--text-mute)' }}>{s.protocol || 'ssh'}</span>
                    <button onClick={(e) => { e.stopPropagation(); disconnect(s.id) }}
                      className="rounded-lg p-1.5 hover:bg-red-50" title="断开"
                      style={{ color: 'var(--text-mute)' }}><Unplug size={14} /></button>
                  </div>
                  {s.device?.os_name && <div className="mt-1 pl-4 text-[11px] truncate" style={{ color: 'var(--text-dim)' }}>{s.device.os_name}</div>}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
