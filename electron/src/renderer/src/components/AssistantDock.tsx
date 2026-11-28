import { useState } from 'react'
import { Bot, Send, Sparkles } from 'lucide-react'

type Msg = { role: 'user' | 'ai'; text: string }

export function AssistantDock() {
  const [msgs, setMsgs] = useState<Msg[]>([
    { role: 'ai', text: '用户已连上助手，无需硬件设备，简单跟我说想做什么，具体指令。' }
  ])
  const [input, setInput] = useState('')

  const send = () => {
    if (!input.trim()) return
    const q = input.trim()
    setMsgs((m) => [...m, { role: 'user', text: q },
      { role: 'ai', text: '助手与经典版共用 Python 内核；完整对话接入中。你可以在「脚本库」/「计划任务」页执行具体操作。' }])
    setInput('')
  }

  return (
    <aside className="w-[300px] shrink-0 flex flex-col"
      style={{ background: 'var(--card)', borderLeft: '1px solid var(--border)' }}>
      <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'var(--brand-soft)' }}>
          <Bot size={16} style={{ color: 'var(--brand)' }} />
        </div>
        <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>AI 智能运维助手</span>
        <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded"
          style={{ background: 'var(--ok-soft)', color: 'var(--ok)' }}>在线</span>
      </div>
      <div className="flex-1 overflow-auto p-4 space-y-3">
        {msgs.map((m, i) => (
          <div key={i} className="rounded-xl px-3 py-2 text-xs leading-relaxed"
            style={m.role === 'user'
              ? { background: 'var(--brand)', color: '#fff', marginLeft: 32 }
              : { background: 'var(--alt)', color: 'var(--text)', marginRight: 16 }}>
            {m.text}
          </div>
        ))}
      </div>
      <div className="p-3" style={{ borderTop: '1px solid var(--border)' }}>
        <div className="flex items-center gap-2 mb-2 text-[11px]" style={{ color: 'var(--text-mute)' }}>
          <Sparkles size={12} /> DeepSeek 官方 · 自动
        </div>
        <div className="flex gap-2">
          <input value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') send() }}
            placeholder="输入指令…" className="tt-input flex-1 !text-xs" />
          <button className="tt-btn tt-btn-primary !px-3" onClick={send}><Send size={13} /></button>
        </div>
      </div>
    </aside>
  )
}
