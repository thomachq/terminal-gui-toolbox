import { useCallback, useEffect, useState } from 'react'
import { Copy, Edit3, Layers3, Play, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api, type Session } from '../api'
import { DataTable, type Column } from '../components/DataTable'
import { Modal } from '../components/Modal'

type Script = Record<string, unknown> & {
  id: string; name: string; kind: string; desc?: string; body: string
  tags?: string[]; builtin?: boolean
}
type Props = { sessions: Session[] }

const blank = { name: '', kind: 'shell', desc: '', tags: '', body: '' }

export function ScriptsPage({ sessions }: Props) {
  const [rows, setRows] = useState<Script[]>([])
  const [form, setForm] = useState<Record<string, string> | null>(null)
  const [err, setErr] = useState('')
  const [output, setOutput] = useState('')
  const [batchSel, setBatchSel] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try { const r = await api.storeList<Script>('scripts'); setRows((r.scripts as Script[]) || []) }
    catch (e) { setErr(String(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  const save = async () => {
    if (!form) return
    if (!form.name.trim() || !form.body.trim()) { setErr('名称和脚本内容不能为空'); return }
    setBusy(true); setErr('')
    try {
      await api.storeUpsert('scripts', {
        ...form, tags: form.tags.split(/[,，\s]+/).filter(Boolean)
      })
      setForm(null); load()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const remove = async (s: Script) => {
    if (s.builtin) { setErr('内置脚本不能删除'); return }
    if (!window.confirm(`删除脚本「${s.name}」？`)) return
    try { await api.storeRemove('scripts', s.id); load() } catch (e) { setErr(String(e)) }
  }

  const run = async (s: Script, sid: string) => {
    if (!sid) { setErr('先在「连接」页连一台设备'); return }
    setBusy(true); setOutput(`▶ ${s.name} @ ${sid}\n`)
    try {
      const cmdRes = await api.core<string>('scripts.shell_cmd', { script: s })
      const cmd = cmdRes.result
      setOutput((o) => o + `$ ${cmd}\n`)
      const res = await api.exec(sid, cmd, 120)
      setOutput((o) => o + (res.out || '') + (res.err || '') + `\n[退出码 ${res.rc} · ${res.ms} ms]\n`)
    } catch (e) { setOutput((o) => o + String(e) + '\n') }
    finally { setBusy(false) }
  }

  const batch = async () => {
    const ids = Object.keys(batchSel).filter((k) => batchSel[k])
    if (ids.length < 2) { setErr('批量执行至少勾选两台已连接的主机'); return }
    setBusy(true); setOutput(`▶ 批量执行 @ ${ids.length} 台\n`)
    try {
      const results = await Promise.all(ids.map(async (sid) => {
        const cmdRes = await api.core<string>('scripts.shell_cmd', { script: form ? form : {} })
        const cmd = cmdRes.result || ''
        const r = await api.exec(sid, cmd, 120)
        return { sid, rc: r.rc, out: r.out, err: r.err }
      }))
      for (const r of results) {
        setOutput((o) => o + `\n── ${r.sid.slice(0, 8)} · rc=${r.rc} ──\n${r.out || r.err || '(空)'}\n`)
      }
    } catch (e) { setOutput((o) => o + String(e) + '\n') }
    finally { setBusy(false) }
  }

  const cols: Column<Script>[] = [
    { key: 'name', title: '名称', width: 180, render: (r) => <b style={{ color: 'var(--brand-deep)' }}>{r.name}{r.builtin ? ' (内置)' : ''}</b> },
    { key: 'kind', title: '类型', width: 90 },
    { key: 'desc', title: '说明', render: (r) => String(r.desc || '—').slice(0, 50) },
    { key: 'tags', title: '标签', width: 130, render: (r) => Array.isArray(r.tags) ? r.tags.join(', ') : String(r.tags || '') },
    { key: 'actions', title: '操作', width: 200, render: (r) => (
      <div className="flex gap-1">
        <button className="tt-btn tt-btn-ghost !px-2 !py-1" onClick={(e) => { e.stopPropagation(); run(r, sessions[0]?.id || '') }} title="在首台连接运行"><Play size={13} /></button>
        <button className="tt-btn tt-btn-ghost !px-2 !py-1" onClick={(e) => { e.stopPropagation(); setForm({ ...blank, id: r.id, name: r.name, kind: r.kind, desc: r.desc || '', body: r.body, tags: Array.isArray(r.tags) ? r.tags.join(', ') : String(r.tags || '') }) }} title="编辑"><Edit3 size={13} /></button>
        <button className="tt-btn tt-btn-ghost !px-2 !py-1" onClick={(e) => { e.stopPropagation(); remove(r) }} title="删除"><Trash2 size={13} /></button>
      </div>
    ) }
  ]

  return (
    <div className="h-full overflow-auto p-7 space-y-4">
      <h1 className="text-xl font-semibold" style={{ color: 'var(--text)' }}>脚本库</h1>
      {err && <div className="rounded-xl px-4 py-2 text-sm" style={{ background: 'var(--err-soft)', color: 'var(--err)' }}>{err}</div>}
      <DataTable columns={cols} rows={rows} rowKey={(r) => r.id}
        searchKeys={['name', 'desc', 'kind']} height={400}
        toolbar={<>
          <button className="tt-btn tt-btn-ghost" onClick={load}><RefreshCw size={14} /> 刷新</button>
          <button className="tt-btn tt-btn-primary" onClick={() => setForm({ ...blank })}><Plus size={14} /> 新建脚本</button>
        </>} />

      <div className="tt-card p-5">
        <div className="flex items-center gap-3 mb-3">
          <Layers3 size={16} style={{ color: 'var(--brand)' }} />
          <span className="text-sm font-medium" style={{ color: 'var(--text)' }}>批量执行</span>
          <span className="text-xs" style={{ color: 'var(--text-mute)' }}>勾选已连接的主机</span>
          <button className="tt-btn tt-btn-primary ml-auto" onClick={batch} disabled={busy}>
            <Copy size={14} /> 批量运行
          </button>
        </div>
        <div className="flex flex-wrap gap-3">
          {sessions.length === 0 && <span className="text-sm" style={{ color: 'var(--text-mute)' }}>还没有连接的设备</span>}
          {sessions.map((s) => (
            <label key={s.id} className="flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg"
              style={{ background: 'var(--alt)', color: 'var(--text)' }}>
              <input type="checkbox" checked={!!batchSel[s.id]}
                onChange={(e) => setBatchSel({ ...batchSel, [s.id]: e.target.checked })} />
              {s.device?.hostname || s.note || s.id.slice(0, 8)}
            </label>
          ))}
        </div>
        {output && <pre className="mt-4 rounded-xl p-4 text-xs whitespace-pre-wrap max-h-72 overflow-auto"
          style={{ background: 'var(--console-bg)', color: 'var(--console-fg)' }}>{output}</pre>}
      </div>

      {form && (
        <Modal title={form.id ? `编辑 · ${form.name}` : '新建脚本'} width={680}
          onClose={() => setForm(null)}
          footer={<>
            <button className="tt-btn tt-btn-ghost" onClick={() => setForm(null)}>取消</button>
            <button className="tt-btn tt-btn-primary" onClick={save} disabled={busy}>{busy ? '保存中…' : '保存'}</button>
          </>}>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <label><span className="text-xs" style={{ color: 'var(--text-dim)' }}>名称 *</span>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="tt-input mt-1 w-full" /></label>
              <label><span className="text-xs" style={{ color: 'var(--text-dim)' }}>类型</span>
                <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} className="tt-input mt-1 w-full">
                  <option value="shell">shell</option><option value="powershell">powershell</option><option value="python">python</option>
                </select></label>
            </div>
            <label><span className="text-xs" style={{ color: 'var(--text-dim)' }}>说明</span>
              <input value={form.desc} onChange={(e) => setForm({ ...form, desc: e.target.value })} className="tt-input mt-1 w-full" /></label>
            <label><span className="text-xs" style={{ color: 'var(--text-dim)' }}>标签（逗号分隔）</span>
              <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} className="tt-input mt-1 w-full" /></label>
            <label><span className="text-xs" style={{ color: 'var(--text-dim)' }}>脚本内容 *</span>
              <textarea value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })}
                rows={10} className="tt-input mt-1 w-full font-mono" /></label>
          </div>
        </Modal>
      )}
    </div>
  )
}
