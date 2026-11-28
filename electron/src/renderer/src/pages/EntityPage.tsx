import { useCallback, useEffect, useState } from 'react'
import { Edit3, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api } from '../api'
import { DataTable, type Column } from '../components/DataTable'
import { Modal } from '../components/Modal'

type Props = {
  entity: 'creds' | 'proxies'
  title: string
  fields: { key: string; label: string; type?: string; options?: string[] }[]
}

export function EntityPage({ entity, title, fields }: Props) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [form, setForm] = useState<Record<string, string> | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try { const r = await api.storeList(entity); setRows((r[entity] as Record<string, unknown>[]) || []) }
    catch (e) { setErr(String(e)) }
  }, [entity])
  useEffect(() => { load() }, [load])

  const save = async () => {
    if (!form) return
    setBusy(true); setErr('')
    try { await api.storeUpsert(entity, form); setForm(null); load() }
    catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  const remove = async (row: Record<string, unknown>) => {
    const key = String(row.id || row.name || '')
    if (!window.confirm(`确定删除「${row.name}」？`)) return
    try { await api.storeRemove(entity, key); load() } catch (e) { setErr(String(e)) }
  }

  const cols: Column<Record<string, unknown>>[] = [
    { key: 'name', title: '名称', width: 160, render: (r) => <b style={{ color: 'var(--brand-deep)' }}>{String(r.name || '')}</b> },
    ...fields.filter((f) => ['kind', 'host', 'port', 'user', 'note'].includes(f.key)).map((f) => ({
      key: f.key, title: f.label, width: f.key === 'note' ? 220 : 110,
      render: (r: Record<string, unknown>) => String(r[f.key] ?? '').slice(0, 40)
    })),
    { key: 'actions', title: '操作', width: 100, render: (r) => (
      <div className="flex gap-1">
        <button className="tt-btn tt-btn-ghost !px-2 !py-1" onClick={(e) => { e.stopPropagation(); setForm(Object.fromEntries(fields.map((f) => [f.key, String(r[f.key] ?? '')]))) }} title="编辑"><Edit3 size={13} /></button>
        <button className="tt-btn tt-btn-ghost !px-2 !py-1" onClick={(e) => { e.stopPropagation(); remove(r) }} title="删除"><Trash2 size={13} /></button>
      </div>
    ) }
  ]

  return (
    <div className="h-full overflow-auto p-7 space-y-4">
      <h1 className="text-xl font-semibold" style={{ color: 'var(--text)' }}>{title}</h1>
      {err && <div className="rounded-xl px-4 py-2 text-sm" style={{ background: 'var(--err-soft)', color: 'var(--err)' }}>{err}</div>}
      <DataTable columns={cols} rows={rows} rowKey={(r) => String(r.id || r.name)}
        searchKeys={['name', 'host', 'user', 'note']}
        toolbar={<>
          <button className="tt-btn tt-btn-ghost" onClick={load}><RefreshCw size={14} /> 刷新</button>
          <button className="tt-btn tt-btn-primary" onClick={() => setForm(Object.fromEntries(fields.map((f) => [f.key, ''])))}><Plus size={14} /> 新增</button>
        </>} />
      {form && (
        <Modal title={`${title} · ${form.id ? '编辑' : '新增'}`} onClose={() => setForm(null)}
          footer={<>
            <button className="tt-btn tt-btn-ghost" onClick={() => setForm(null)}>取消</button>
            <button className="tt-btn tt-btn-primary" onClick={save} disabled={busy}>{busy ? '保存中…' : '保存'}</button>
          </>}>
          <div className="grid grid-cols-2 gap-4">
            {fields.map((f) => (
              <label key={f.key} className={f.key === 'note' ? 'col-span-2' : ''}>
                <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{f.label}</span>
                {f.options ? (
                  <select value={form[f.key] || ''} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                    className="tt-input mt-1 w-full">
                    {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : (
                  <input value={form[f.key] || ''} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                    type={f.type || 'text'} className="tt-input mt-1 w-full" />
                )}
              </label>
            ))}
          </div>
        </Modal>
      )}
    </div>
  )
}
