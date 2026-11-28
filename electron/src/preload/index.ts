import { contextBridge, ipcRenderer } from 'electron'

type Config = { ok: boolean; port: number; token: string; error?: string }

contextBridge.exposeInMainWorld('ttb', {
  config: (): Promise<Config> => ipcRenderer.invoke('ttb:config'),
  openClassic: (): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('ttb:open-classic')
})
