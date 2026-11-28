import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Edit3, Play, Plus, RefreshCw, Trash2, ToggleLeft } from 'lucide-react'
import { api, type Session } from '../api'
import { DataTable, type Column } from '../components/DataTable'
import { Modal } from '../components/Modal'

type Job = Record<string, unknown> & {
  id: string; source: string; schedule: string; command: string
  user?: string; enabled: boolean; raw: string; unit?: string
}
type Props = { sid: string; sessions: Session[] }

const blank = { schedule: '0 3 * * *', command: '', user: 'root' }

export function CronPage({ sid, sessions }: Props) {
  const [rows, setRows] = useState<Job[]>([])
  const [rawText, setRawText] = useState('')
  const [form, setForm] = useState<(Record<string, string> & { id?: string }) | null>(null)
  const [err, setErr] = useState('')
  const [log, setLog] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  const appendLog = (s: string) => setLog((old) => [s, ...old].slice(0, 40))

  const load = useCallback(async () => {
    if (!sid) { setErr('先在「连接」页连一台设备'); return }
    setBusy(true); setErr('')
    try {
      const res = await api.exec(sid,
        "echo '@@crontab'; crontab -l 2>/dev/null; echo '@@etccrontab'; cat /etc/crontab 2>/dev/null; echo '@@done'", 30)
      const text = res.out || ''
      setRawText(text)
      const parsed = await api.core<string>('cron.parse_bundle', { text })
      const jobs = await api.core<Job[]>('cron.jobs_from_bundle',
        { bundle: parsed.result || {} })
      setRows(jobs.result || [])
      appendLog(`[读取] ${jobs.result?.length || 0} 条任务`)
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }, [sid])

  useEffect(() => { if (sid) load() }, [load, sid])

  const write = async (key: string, text: string, label: string) => {
    setBusy(true)
    try {
      const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 13)
      const bk = await api.core<string>('cron.backup_cmd', { key, stamp })
      const br = await api.exec(sid, bk.result, 30)
      const backupPath = (br.out || '').trim().split('\n').filter(Boolean).pop() || ''
      if (key !== 'crontab' && (br.rc !== 0 || !backupPath)) {
        appendLog('[备份] 失败，未写入'); setErr('备份失败，原文未改动'); return
      }
      appendLog(`[备份] ${backupPath || '无旧文可备'}`)
      const wc = await api.core<string>('cron.write_cmd_for', { key, text })
      const wr = await api.exec(sid, wc.result, 45)
      if (wr.rc === 0) {
        appendLog(`[写入] ${label} 成功`)
        const hint = await api.core<string>('cron.rollback_hint', { key, backupPath })
        appendLog(`[回滚] ${hint.result}`)
        load()
      } else {
        const hint = await api.core<string>('cron.rollback_hint', { key, backupPath })
        setErr(`${wr.err || wr.out || '写入失败'}\n回滚：${hint.result}`)
        appendLog(`[写入] 失败：${wr.err || wr.out}`)
      }
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const save = async () => {
    if (!form) return
    const check = await api.core<string | null>('cron.validate', { spec: form.schedule })
    if (check.result) { setErr(check.result); return }
    const line = await api.core<string>('cron.build_line',
      { schedule: form.schedule, command: form.command, user: form.user || '' })
    const newText = form.id
      ? await api.core<string>('cron.replace_raw', { text: rawText, raw: form.id, new_line: line.result })
      : { result: rawText + '\n' + line.result + '\n' }
    if (!newText.result || newText.result === rawText) { setErr('没找到这一行，先刷新'); return }
    setForm(null)
    await write('crontab', newText.result, form.id ? '编辑任务' : '新增任务')
  }

  const toggle = async (r: Job) => {
    const t = await api.core<string>('cron.toggle_raw', { text: rawText, raw: r.raw })
    await write('crontab', t.result, r.enabled ? '停用' : '启用')
  }

  const cols: Column<Job>[] = [
    { key: 'schedule', title: '调度', width: 130, render: (r) => <code>{r.schedule}</code> },
    { key: 'command', title: '命令', render: (r) => String(r.command).slice(0, 60) },
    { key: 'user', title: '用户', width: 80 },
    { key: 'source', title: '位置', width: 90 },
    { key: 'enabled', title: '状态', width: 70, render: (r) => r.enabled ? '启用' : '停用' },
    { key: 'actions', title: '操作', width: 150, render: (r) => (
      <div className="flex gap-1">
        <button className="tt-btn tt-btn-ghost !px-2 !py-1" onClick={(e) => { e.stopPropagation(); toggle(r) }} title="启停"><ToggleLeft size={13} /></button>
        <button className="tt-btn tt-btn-ghost !px-2 !py-1" onClick={(e) => { e.stopPropagation(); setForm({ ...blank, id: r.raw, schedule: r.schedule, command: r.command, user: r.user || '' }) }} title="编辑"><Edit3 size={13} /></button>
        <button className="tt-btn tt-btn-ghost !px-2 !py-1" onClick={(e) => { e.stopPropagation(); api.exec(sid, r.command, 60).then((x) => setErr(`执行 rc=${x.rc}\n${x.out || x.err}`)) }} title="立即执行"><Play size={13} /></button>
      </div>
    ) }
  ]

  return (
    <div className="h-full overflow-auto p-7 space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold" style={{ color: 'var(--text)' }}>计划任务</h1>
        <span className="text-xs" style={{ color: 'var(--text-mute)' }}>
          {sessions.find((s) => s.id === sid)?.device?.hostname || sid.slice(0, 8) || '未连接'}
        </span>
      </div>
      <div className="flex items-start gap-2 text-xs px-4 py-3 rounded-xl"
        style={{ background: 'var(--warn-soft)', color: 'var(--warn)' }}>
        <AlertTriangle size={15} className="shrink-0 mt-0.5" />
        写远端系统状态：每次写入前自动备份并给出回滚命令；只改目标行，保持停用状态。
      </div>
      {err && <pre className="rounded-xl px-4 py-2 text-sm whitespace-pre-wrap"
        style={{ background: 'var(--err-soft)', color: 'var(--err)' }}>{err}</pre>}
      <DataTable columns={cols} rows={rows} rowKey={(r) => r.id}
        searchKeys={['schedule', 'command', 'user', 'source']} height={420}
        toolbar={<>
          <button className="tt-btn tt-btn-ghost" onClick={load} disabled={busy}><RefreshCw size={14} /> 刷新</button>
          <button className="tt-btn tt-btn-primary" onClick={() => setForm({ ...blank })} disabled={!sid}><Plus size={14} /> 新增任务</button>
        </>} />
      <div className="tt-card p-5">
        <div className="text-sm font-medium mb-2" style={{ color: 'var(--text)' }}>操作记录</div>
        <pre className="text-xs whitespace-pre-wrap max-h-40 overflow-auto"
          style={{ color: 'var(--text-dim)' }}>{log.join('\n') || '（暂无操作）'}</pre>
      </div>

      {form && (
        <Modal title={form.id ? '编辑计划任务' : '新增计划任务'}
          subtitle="写前自动备份；停用状态保持不变"
          onClose={() => setForm(null)}
          footer={<>
            <button className="tt-btn tt-btn-ghost" onClick={() => setForm(null)}>取消</button>
            <button className="tt-btn tt-btn-primary" onClick={save} disabled={busy}>备份并写入</button>
          </>}>
          <div className="space-y-4">
            <label className="block"><span className="text-xs" style={{ color: 'var(--text-dim)' }}>调度（分 时 日 月 周）</span>
              <input value={form.schedule} onChange={(e) => setForm({ ...form, schedule: e.target.value })}
                className="tt-input mt-1 w-full font-mono" /></label>
            <label className="block"><span className="text-xs" style={{ color: 'var(--text-dim)' }}>命令</span>
              <input value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })}
                className="tt-input mt-1 w-full font-mono" /></label>
            <label className="block"><span className="text-xs" style={{ color: 'var(--text-dim)' }}>运行用户（系统级任务才有）</span>
              <input value={form.user} onChange={(e) => setForm({ ...form, user: e.target.value })}
                className="tt-input mt-1 w-full" /></label>
          </div>
        </Modal>
      )}
    </div>
  )
}
