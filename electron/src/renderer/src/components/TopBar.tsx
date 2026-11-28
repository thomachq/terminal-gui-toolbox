import { ChevronDown, Settings2, PanelRightClose, PanelRightOpen } from 'lucide-react'
import type { Session } from '../api'

type Props = {
  sessions: Session[]; active: string; onActive: (id: string) => void
  assistantOpen: boolean; onAssistant: () => void
}

export function TopBar({ sessions, active, onActive, assistantOpen, onAssistant }: Props) {
  const cur = sessions.find((s) => s.id === active)
  return (
    <div className="shrink-0 px-5 py-3"
      style={{ background: 'var(--card)', borderBottom: '1px solid var(--border)' }}>
      <div className="flex items-start gap-4">
        <div className="min-w-0">
          <h1 className="text-lg font-bold leading-tight" style={{ color: 'var(--text)' }}>Terminal GUI Toolbox</h1>
          <p className="text-[11px] mt-1 truncate" style={{ color: 'var(--text-mute)' }}>
            多协议终端工具箱 · SSH / Telnet / 本机 / 设备调试 / 设备探测 / 安全中心 / 数据库 / 计划任务 / Web 服务 / AI 运维
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2 shrink-0">
          {cur && (
            <span className="text-[11px] px-2.5 py-1 rounded-full"
              style={{ background: 'var(--ok-soft)', color: 'var(--ok)' }}>
              ● SSH
            </span>
          )}
          {cur?.device?.os_name && (
            <span className="text-[11px] px-2.5 py-1 rounded-full max-w-[220px] truncate"
              style={{ background: 'var(--brand-soft)', color: 'var(--brand-deep)' }}
              title={cur.device.os_name}>● {cur.device.os_name}</span>
          )}
          <button className="tt-btn tt-btn-ghost !py-1.5 !text-xs" onClick={onAssistant}>
            {assistantOpen ? <PanelRightClose size={13} /> : <PanelRightOpen size={13} />}
            {assistantOpen ? '收起助手' : '展开助手'}
          </button>
          <button className="tt-btn tt-btn-ghost !py-1.5 !text-xs"><Settings2 size={13} /> 设置</button>
        </div>
      </div>

      {sessions.length > 0 && (
        <div className="flex items-center gap-1 mt-2">
          {sessions.map((s) => (
            <button key={s.id} onClick={() => onActive(s.id)}
              className="text-[11px] px-3 py-1 rounded-t-lg"
              style={s.id === active
                ? { background: 'var(--brand-soft)', color: 'var(--brand-deep)', borderBottom: '2px solid var(--brand)' }
                : { color: 'var(--text-dim)' }}>
              {s.device?.hostname || s.note || s.id.slice(0, 8)}
            </button>
          ))}
          <ChevronDown size={13} style={{ color: 'var(--text-mute)' }} />
          <span className="ml-auto text-[11px]" style={{ color: 'var(--text-mute)' }}>
            {sessions.length} 台已连接
          </span>
        </div>
      )}
    </div>
  )
}
