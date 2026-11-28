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
      theme: { background: '#0E1A2B', foreground: '#D7E3F4', cursor: '#7DD3FC',
        selectionBackground: '#3A66AC55', green: '#86EFAC', red: '#FCA5A5',
        cyan: '#7DD3FC' }
    })
    const fit = new FitAddon()
    t.loadAddon(fit)
    t.open(host.current)
    fit.fit()
    term.current = t
    const prompt = () => t.write(`\r\n\x1b[38;2;125;211;252m${label}\x1b[0m \x1b[38;2;134;239;172m❯\x1b[0m `)
    t.writeln('\x1b[38;2;143;163;191mTerminal GUI Toolbox · Electron（与经典版共用内核）\x1b[0m')
    t.writeln('\x1b[38;2;143;163;191m命令式终端：每条命令走内核会话，与经典版行为一致\x1b[0m')
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
      <div className="h-14 shrink-0 flex items-center gap-3 px-6"
        style={{ background: 'var(--card)', borderBottom: '1px solid var(--border)' }}>
        <TerminalSquare size={17} style={{ color: 'var(--brand)' }} />
        <span className="text-sm font-medium" style={{ color: 'var(--text)' }}>{label}</span>
        <span className="text-[11px]" style={{ color: 'var(--text-mute)' }}>{sid.slice(0, 8)}</span>
        {disconnected && (
          <span className="ml-auto flex items-center gap-1.5 text-xs" style={{ color: 'var(--err)' }}>
            <CircleAlert size={14} /> 会话已断开，请回连接页重连
          </span>
        )}
      </div>
      <div ref={host} className="flex-1 min-h-0" style={{ background: 'var(--console-bg)' }} />
    </div>
  )
}
