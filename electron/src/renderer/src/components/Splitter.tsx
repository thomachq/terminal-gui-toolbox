import { useCallback, useEffect, useRef, useState } from 'react'

type Dir = 'horizontal' | 'vertical'

type Props = {
  dir: Dir
  size: number
  onSize: (n: number) => void
  min: number
  max?: number
  storageKey?: string
  invert?: boolean          // true = 向右拖会让 size 变小（助手在右侧）
}

/** 与经典版 PanedWindow 对齐：6px 分隔条、手型光标、松手记忆、最小尺寸、窗口变窄时自动收敛。 */
export function Splitter({ dir, size, onSize, min, max, storageKey, invert }: Props) {
  const drag = useRef<{ start: number; initial: number } | null>(null)
  const latest = useRef(size)
  const [hover, setHover] = useState(false)
  const [dragging, setDragging] = useState(false)
  const horiz = dir === 'horizontal'

  useEffect(() => { latest.current = size }, [size])

  const clamp = useCallback((n: number) => {
    const hi = max || (horiz ? window.innerWidth - 200 : window.innerHeight - 120)
    return Math.max(min, Math.min(hi, n))
  }, [min, max, horiz])

  useEffect(() => {
    if (!storageKey) return
    const saved = localStorage.getItem(storageKey)
    if (saved != null) {
      const v = clamp(Number(saved))
      if (Number.isFinite(v)) onSize(v)
    }
  }, [storageKey, clamp, onSize])

  useEffect(() => {
    if (!dragging) return
    const move = (e: MouseEvent) => {
      if (!drag.current) return
      const delta = horiz ? e.clientX - drag.current.start : e.clientY - drag.current.start
      onSize(clamp(drag.current.initial + (invert ? -delta : delta)))
    }
    const up = () => {
      if (drag.current && storageKey) {
        // 松手才记忆，与经典版 `_ai_sash_done` 一致
        localStorage.setItem(storageKey, String(latest.current))
      }
      drag.current = null
      setDragging(false)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
  }, [dragging, horiz, invert, clamp, onSize, storageKey])

  // 窗口变窄时收敛，避免助手/日志把主区挤没（经典版 `_ai_dock_clamp`）
  useEffect(() => {
    const onResize = () => onSize(clamp(latest.current))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [clamp, onSize])

  return (
    <div
      onMouseDown={(e) => {
        e.preventDefault()
        drag.current = { start: horiz ? e.clientX : e.clientY, initial: size }
        setDragging(true)
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title="拖动调整大小（松手记住宽度）"
      className={horiz ? 'shrink-0 cursor-col-resize' : 'shrink-0 cursor-row-resize'}
      style={{
        width: horiz ? 6 : '100%', height: horiz ? '100%' : 6,
        background: dragging || hover ? 'var(--brand)' : 'var(--border2)',
        transition: dragging ? 'none' : 'background .15s'
      }}
    />
  )
}
