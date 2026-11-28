/** 与 wbtui.py 的 LIGHT / DARK 调色板逐值对齐（WorkBuddy clear-blue 皮肤）。 */
export const LIGHT = {
  page: '#F5F9FF', card: '#FFFFFF', alt: '#F4F8FE',
  border: '#DCE8FF', border2: '#C9DCF7',
  brand: '#3A66AC', brandDeep: '#22406E', brandHover: '#4E79BE',
  brandSoft: '#E6EFFF', brandSoft2: '#DCEAFF',
  text: '#001F4F', textDim: '#5B7093', textMute: '#8A9BB5',
  ok: '#12855C', okSoft: '#E4F5EC',
  warn: '#9A7412', warnSoft: '#FCF3D7',
  err: '#D93B34', errSoft: '#FDECEB',
  inputBg: '#FFFFFF', inputBorder: '#DCE8FF',
  consoleBg: '#0E1A2B', consoleFg: '#D7E3F4',
  shadow: '#E8F0FE'
} as const

export const DARK = {
  page: '#0F1729', card: '#16203A', alt: '#1B2742',
  border: '#243354', border2: '#2F4068',
  brand: '#5B8BD6', brandDeep: '#BFD4F5', brandHover: '#6E9BE0',
  brandSoft: '#1E2C4C', brandSoft2: '#26375C',
  text: '#E8EFFB', textDim: '#9FB0CC', textMute: '#7186A6',
  ok: '#4ADE80', okSoft: '#14301F',
  warn: '#FBBF24', warnSoft: '#33280B',
  err: '#F87171', errSoft: '#3A1616',
  inputBg: '#111B2F', inputBorder: '#2A3A5C',
  consoleBg: '#0A1020', consoleFg: '#D7E3F4',
  shadow: '#0B1220'
} as const

export type Palette = typeof LIGHT | typeof DARK

export function applyTheme(p: Palette): void {
  const r = document.documentElement.style
  r.setProperty('--page', p.page)
  r.setProperty('--card', p.card)
  r.setProperty('--alt', p.alt)
  r.setProperty('--border', p.border)
  r.setProperty('--border2', p.border2)
  r.setProperty('--brand', p.brand)
  r.setProperty('--brand-deep', p.brandDeep)
  r.setProperty('--brand-hover', p.brandHover)
  r.setProperty('--brand-soft', p.brandSoft)
  r.setProperty('--brand-soft2', p.brandSoft2)
  r.setProperty('--text', p.text)
  r.setProperty('--text-dim', p.textDim)
  r.setProperty('--text-mute', p.textMute)
  r.setProperty('--ok', p.ok)
  r.setProperty('--ok-soft', p.okSoft)
  r.setProperty('--warn', p.warn)
  r.setProperty('--warn-soft', p.warnSoft)
  r.setProperty('--err', p.err)
  r.setProperty('--err-soft', p.errSoft)
  r.setProperty('--input-bg', p.inputBg)
  r.setProperty('--input-border', p.inputBorder)
  r.setProperty('--console-bg', p.consoleBg)
  r.setProperty('--console-fg', p.consoleFg)
  r.setProperty('--shadow', p.shadow)
}
