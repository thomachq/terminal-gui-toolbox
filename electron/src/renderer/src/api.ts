export type Config = { ok: boolean; port: number; token: string; error?: string }
export type Session = {
  id: string
  protocol?: string
  note?: string
  device?: { hostname?: string; os_name?: string; os_family?: string }
  [k: string]: unknown
}

declare global {
  interface Window {
    ttb: {
      config: () => Promise<Config>
      openClassic: () => Promise<{ ok: boolean; error?: string }>
      onMenu: (fn: (action: string) => void) => void
    }
  }
}

let cfg: Config | null = null

export async function initConfig(): Promise<Config> {
  cfg = await window.ttb.config()
  return cfg
}

export function getConfig(): Config {
  return cfg || { ok: false, port: 0, token: '', error: '桥未就绪' }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const c = getConfig()
  if (!c.ok) throw new Error(c.error || '桥未就绪')
  const res = await fetch(`http://127.0.0.1:${c.port}${path}`, {
    method,
    headers: { Authorization: `Bearer ${c.token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(String((data as { error?: string }).error || `HTTP ${res.status}`))
  return data as T
}

export const api = {
  health: () => call<{ ok: boolean }>('GET', '/v1/health'),
  sessions: () => call<{ sessions: Session[]; count: number }>('GET', '/v1/sessions'),
  hosts: () => call<{ hosts?: unknown[]; count?: number }>('GET', '/v1/hosts'),
  connect: (spec: Record<string, unknown>) =>
    call<{ id: string; device?: Session['device'] }>('POST', '/v1/sessions', spec),
  close: (id: string) => call<{ closed: boolean }>('DELETE', `/v1/sessions/${id}`),
  exec: (id: string, cmd: string, timeout = 60) =>
    call<{ rc: number; out: string; err: string; ms: number }>(
      'POST', `/v1/sessions/${id}/exec`, { cmd, timeout }),

  // core 白名单桥：全功能页面的通用入口（68 个函数，core 一行不重写）
  coreCatalog: () => call<{ items: { name: string; desc: string; session: boolean }[]; count: number }>(
    'GET', '/v1/tools/core/catalog'),
  core: <T = unknown>(name: string, args: Record<string, unknown> = {}, session = '') =>
    call<{ ok: boolean; result: T }>('POST', '/v1/tools/core',
      session ? { name, args, session } : { name, args }),

  // 通用实体 CRUD（档案/凭证/脚本/代理/审计）
  storeList: <T = Record<string, unknown>>(entity: string) =>
    call<Record<string, T[] | number>>('GET', `/v1/tools/store/${entity}`),
  storeGet: <T = Record<string, unknown>>(entity: string, key: string) =>
    call<{ record: T }>('GET', `/v1/tools/store/${entity}/get?key=${encodeURIComponent(key)}`),
  storeUpsert: (entity: string, rec: Record<string, unknown>) =>
    call<{ ok: boolean; key: string }>('POST', `/v1/tools/store/${entity}`, rec),
  storeRemove: (entity: string, key: string) =>
    call<{ ok: boolean }>('DELETE', `/v1/tools/store/${entity}?key=${encodeURIComponent(key)}`),

  // 常用 tools 端点
  sysinfo: (session?: string) =>
    call<Record<string, unknown>>('GET', `/v1/tools/sysinfo${session ? `?session=${session}` : ''}`),
  docker: (what = 'all', session?: string) =>
    call<Record<string, unknown>>('GET', `/v1/tools/docker?what=${what}${session ? `&session=${session}` : ''}`),
  services: (session?: string) =>
    call<Record<string, unknown>>('GET', `/v1/tools/services${session ? `?session=${session}` : ''}`)
}
