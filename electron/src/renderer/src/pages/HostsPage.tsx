import { useCallback, useEffect, useState } from 'react'
import { Cable, Edit3, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api, type Session } from '../api'
import { DataTable, type Column } from '../components/DataTable'
import { Modal } from '../components/Modal'

type Host = Record<string, unknown> & {
  name: string; host?: string; port?: number; user?: string; proto?: string
  auth?: string; group?: string; note?: string; os_name?: string
}

type Props = { sessions: Session[]; onConnect: () => void; reloadSessions: () => void }

const blank: Record<string, string> = {
  name: '', host: '', port: '22', user: 'root', proto: 'ssh',
  auth: 'password', password: '', key_path: '', note: '', group: ''
}

export function HostsPage({ sessions, onConnect, reloadSessions }: Props) {
  const [rows, setRows] = useState<Host[]>([])
  const [selected, setSelected] = useState('')
  const [form, setForm] = useState<Record<string, string> | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try { const r = await api.storeList<Host>('hosts'); setRows((r.hosts as Host[]) || []) }
    catch (e) { setErr(String(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  const save = async () => {
    if (!form) return
    if (!form.name.trim() || !form.host.trim()) { setErr('名称和主机不能为空'); return }
    setBusy(true); setErr('')
    try {
      await api.storeUpsert('hosts', {
        ...form, port: Number(form.port || 22)
      })
      setForm(null); load()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const remove = async (row: Host) => {
    if (!window.confirm(`确定删除档案「${row.name}」？`)) return
    try { await api.storeRemove('hosts', row.name); load() } catch (e) { setErr(String(e)) }
  }

  const connect = async (row: Host) => {
    setErr(''); setBusy(true)
    try {
      await api.connect({
        protocol: row.proto || 'ssh', host: row.host || '', port: Number(row.port || 22),
        user: row.user || '', auth: row.auth || 'password',
        note: row.name
      })
      onConnect(); reloadSessions()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const cols: Column<Host>[] = [
    { key: 'name', title: '名称', width: 150, render: (r) => <b style={{ color: 'var(--brand-deep)' }}>{r.name}</b> },
    { key: 'host', title: '地址', render: (r) => `${r.host || ''}:${r.port || ''}` },
    { key: 'user', title: '用户', width: 100 },
    { key: 'proto', title: '协议', width: 80 },
    { key: 'group', title: '分组', width: 100 },
    { key: 'os_name', title: '系统', render: (r) => String(r.os_name || '—').slice(0, 30) },
    { key: 'actions', title: '操作', width: 150, render: (r) => (
      <div className="flex gap-1">
        <button className="tt-btn tt-btn-ghost !px-2 !py-1" onClick={(e) => { e.stopPropagation(); connect(r) }}
          title="连接"><Cable size={13} /></button>
        <button className="tt-btn tt-btn-ghost !px-2 !py-1" onClick={(e) => { e.stopPropagation(); setForm({ ...blank, ...r, port: String(r.port || 22) }) }}
          title="编辑"><Edit3 size={13} /></button>
        <button className="tt-btn tt-btn-ghost !px-2 !py-1" onClick={(e) => { e.stopPropagation(); remove(r) }}
          title="删除"><Trash2 size={13} /></button>
      </div>
    ) }
  ]

  return (
    <div className="h-full overflow-auto p-7 space-y-4">
      <div className="flex items-center gap-3">
        <div>
          <h1 className="text-xl font-semibold" style={{ color: 'var(--text)' }}>主机管理</h1>
          <p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>
            与经典版共用 profiles.dat（加密）；口令不会在列表里回传。
          </p>
        </div>
      </div>
      {err && <div className="rounded-xl px-4 py-2 text-sm" style={{ background: 'var(--err-soft)', color: 'var(--err)' }}>{err}</div>}
      <DataTable columns={cols} rows={rows} rowKey={(r) => r.name}
        selectedKey={selected} onRowClick={(r) => setSelected(r.name)}
        searchKeys={['name', 'host', 'user', 'group', 'os_name']}
        toolbar={<>
          <button className="tt-btn tt-btn-ghost" onClick={load}><RefreshCw size={14} /> 刷新</button>
          <button className="tt-btn tt-btn-primary" onClick={() => setForm({ ...blank })}><Plus size={14} /> 添加主机</button>
        </>} />

      {form && (
        <Modal title={form.name ? `编辑 · ${form.name}` : '添加主机'}
          subtitle="保存后写入加密档案，与经典版完全一致"
          onClose={() => setForm(null)}
          footer={<>
            <button className="tt-btn tt-btn-ghost" onClick={() => setForm(null)}>取消</button>
            <button className="tt-btn tt-btn-primary" onClick={save} disabled={busy}>{busy ? '保存中…' : '保存'}</button>
          </>}>
          <div className="grid grid-cols-2 gap-4">
            {([
              ['name', '名称 *'], ['host', '主机地址 *'], ['port', '端口'], ['user', '用户名'],
              ['proto', '协议'], ['auth', '认证方式'], ['password', '密码'], ['key_path', '私钥路径'],
              ['group', '分组'], ['note', '备注']
            ] as [string, string][]).map(([key, label]) => (
              <label key={key} className={key === 'note' ? 'col-span-2' : ''}>
                <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{label}</span>
                <input value={form[key] || ''} onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                  type={key === 'password' ? 'password' : 'text'}
                  className="tt-input mt-1 w-full" />
              </label>
            ))}
          </div>
        </Modal>
      )}
    </div>
  )
}
