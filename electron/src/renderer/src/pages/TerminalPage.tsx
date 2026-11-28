import { useEffect, useRef, useState } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { CircleAlert, TerminalSquare } from 'lucide-react'
import { api } from '../api'
import '@xterm/xterm/css/xterm.css'

type Props = { sid: string; label: string }

export function TerminalPage({ sid, label }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const term = useRef<XTerm | null>(null)
  const line = useRef('')
  const busy = useRef(false)
  const sidRef = useRef(sid)
  const [disconnected, setDisconnected] = useState(false)
  sidRef.current = sid

  useEffect(() => {
    if (!host.current) return
    const t = new XTerm({
      fontFamily: 'Cascadia Code, Consolas, monospace', fontSize: 13,
      cursorBlink: true, scrollback: 5000,
      theme: { background: '#0b1220', foreground: '#d7dee9', cursor: '#a78bfa',
        selectionBackground: '#5b21b655', green: '#4ade80', red: '#f87171' }
    })
    const fit = new FitAddon()
    t.loadAddon(fit)
    t.open(host.current)
    fit.fit()
    term.current = t
    const prompt = () => t.write(`\r\n\x1b[38;2;167;139;250m${label}\x1b[0m \x1b[38;2;52;211;153m❯\x1b[0m `)
    t.writeln('\x1b[38;2;148;163;184mTerminal GUI Toolbox · Electron Phase 1\x1b[0m')
    t.writeln('\x1b[38;2;100;116;139m命令式终端：每条命令走 /v1/sessions/<id>/exec，与 Tk 版行为一致\x1b[0m')
    prompt()

    const sub = t.onData(async (data) => {
      if (busy.current) return
      if (data === '\r') {
        const cmd = line.current.trim()
        line.current = ''
        if (!cmd) { prompt(); return }
        busy.current = true
        t.write('\r\n')
        try {
          const res = await api.exec(sidRef.current, cmd, 60)
          if (res.out) t.write(res.out.replace(/\n/g, '\r\n'))
          if (res.err) t.write(`\x1b[31m${res.err.replace(/\n/g, '\r\n')}\x1b[0m`)
          if (res.rc !== 0) t.write(`\x1b[31m[退出码 ${res.rc}]\x1b[0m\r\n`)
        } catch (e) {
          t.write(`\x1b[31m${e instanceof Error ? e.message : String(e)}\x1b[0m\r\n`)
          setDisconnected(true)
        } finally {
          busy.current = false
          prompt()
        }
        return
      }
      if (data === '\u007f') {
        if (line.current.length) { line.current = line.current.slice(0, -1); t.write('\b \b') }
        return
      }
      if (data === '\u0003') { line.current = ''; t.write('^C'); prompt(); return }
      line.current += data
      t.write(data)
    })
    const onResize = () => fit.fit()
    window.addEventListener('resize', onResize)
    return () => { sub.dispose(); window.removeEventListener('resize', onResize); t.dispose(); term.current = null }
  }, [label])

  return (
    <div className="h-full flex flex-col">
      <div className="h-14 shrink-0 flex items-center gap-3 px-6 border-b border-slate-800/80 bg-[#0e1420]">
        <TerminalSquare size={17} className="text-violet-400" />
        <span className="text-sm text-slate-200">{label}</span>
        <span className="text-[11px] text-slate-500">{sid.slice(0, 8)}</span>
        {disconnected && (
          <span className="ml-auto flex items-center gap-1.5 text-xs text-red-400">
            <CircleAlert size={14} /> 会话已断开，请回连接页重连
          </span>
        )}
      </div>
      <div ref={host} className="flex-1 min-h-0 bg-[#0b1220]" />
    </div>
  )
}
