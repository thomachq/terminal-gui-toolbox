import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { app, BrowserWindow, ipcMain } from 'electron'

const here = dirname(fileURLToPath(import.meta.url))

function findRepoRoot(from: string): string {
  // 打包后 __dirname = electron/out/main（往上两级只到 electron/），
  // dev 下又可能不同。向上找真正带 app.py 的那层，别硬编码层数。
  let dir = from
  for (let i = 0; i < 6; i++) {
    if (existsSync(join(dir, 'app.py')) && existsSync(join(dir, 'electron', 'bridge', 'api_main.py'))) return dir
    const parent = dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return resolve(from, '..', '..')
}

const repoRoot = findRepoRoot(here)
const bridgeScript = join(repoRoot, 'electron', 'bridge', 'api_main.py')

type BridgeInfo = { ok: boolean; port: number; token: string; message?: string; error?: string }
let bridge: ChildProcess | null = null
let info: BridgeInfo | null = null

function pythonExe(): string {
  return process.env.TTB_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
}

function startBridge(): Promise<BridgeInfo> {
  return new Promise((done) => {
    if (!existsSync(bridgeScript)) {
      done({ ok: false, port: 0, token: '', error: `找不到桥脚本：${bridgeScript}` })
      return
    }
    const child = spawn(pythonExe(), [bridgeScript], {
      cwd: repoRoot, env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      stdio: ['ignore', 'pipe', 'pipe']
    })
    bridge = child
    let buf = ''
    let settled = false
    const finish = (v: BridgeInfo) => {
      if (settled) return
      settled = true
      done(v)
    }
    child.stdout?.on('data', (chunk: Buffer) => {
      buf += chunk.toString('utf-8')
      const i = buf.indexOf('\n')
      if (i < 0) return
      try {
        const obj = JSON.parse(buf.slice(0, i).trim()) as BridgeInfo
        if (obj.ok) done(obj)
        else finish(obj)
      } catch {
        finish({ ok: false, port: 0, token: '', error: '桥的启动输出不是 JSON' })
      }
    })
    child.stderr?.on('data', (chunk: Buffer) => {
      // 令牌绝不写日志；stderr 只留最后 500 字节给排障
      const text = chunk.toString('utf-8').slice(-500)
      console.error('[bridge]', text.replace(/ttb_[A-Za-z0-9_-]+/g, '<token>'))
    })
    child.on('exit', (code) => {
      if (!settled) finish({ ok: false, port: 0, token: '', error: `桥提前退出 rc=${code}` })
    })
    setTimeout(() => finish({ ok: false, port: 0, token: '', error: '桥启动超时（20s）' }), 20000)
  })
}

function stopBridge(): void {
  if (bridge && !bridge.killed) {
    bridge.kill()
    bridge = null
  }
}

async function createWindow(): Promise<void> {
  const win = new BrowserWindow({
    width: 1360, height: 860, minWidth: 960, minHeight: 620,
    title: 'Terminal GUI Toolbox · Electron',
    backgroundColor: '#0b0f19',
    webPreferences: {
      preload: join(here, '..', 'preload', 'index.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: false
    }
  })
  const boot = await startBridge()
  info = boot
  win.loadFile(join(here, '..', 'renderer', 'index.html'))
  if (process.env.TTB_DEV === '1') win.webContents.openDevTools()
}

ipcMain.handle('ttb:config', () => info)

ipcMain.handle('ttb:open-classic', () => {
  // Phase 1 只移植了连接页 + 终端页；其余功能在 Tkinter 版**一行没删**，
  // 给用户一个明确的回去入口，别让他以为功能丢了。
  const candidates = [
    join(repoRoot, 'TerminalToolbox.exe'),
    'D:\\Tools\\Terminal-GUI-Toolbox\\TerminalToolbox.exe'
  ]
  const exe = candidates.find((p) => existsSync(p))
  if (!exe) return { ok: false, error: '找不到经典版 TerminalToolbox.exe' }
  const child = spawn(exe, [], { detached: true, stdio: 'ignore' })
  child.unref()
  return { ok: true }
})

app.whenReady().then(() => {
  createWindow().catch((e) => console.error('启动失败', e))
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow().catch(() => {})
  })
})

app.on('window-all-closed', () => {
  stopBridge()
  if (process.platform !== 'darwin') app.quit()
})
app.on('before-quit', stopBridge)
