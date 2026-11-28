import { useEffect, type ReactNode } from 'react'
import { X } from 'lucide-react'

type Props = {
  title: string; subtitle?: string; onClose: () => void
  children: ReactNode; footer?: ReactNode; width?: number
}

export function Modal({ title, subtitle, onClose, children, footer, width = 560 }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-8"
      style={{ background: 'rgba(0,31,79,.35)' }} onClick={onClose}>
      <div className="tt-card flex flex-col max-h-full" style={{ width }}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3 px-6 py-4"
          style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex-1">
            <div className="text-base font-semibold" style={{ color: 'var(--text)' }}>{title}</div>
            {subtitle && <div className="text-xs mt-1" style={{ color: 'var(--text-mute)' }}>{subtitle}</div>}
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-red-50"
            style={{ color: 'var(--text-mute)' }}><X size={17} /></button>
        </div>
        <div className="flex-1 overflow-auto px-6 py-5">{children}</div>
        {footer && <div className="flex justify-end gap-2 px-6 py-4"
          style={{ borderTop: '1px solid var(--border)' }}>{footer}</div>}
      </div>
    </div>
  )
}
