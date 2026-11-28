import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { app, BrowserWindow, ipcMain, Menu } from 'electron'

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
      cwd: repoRoot,
      // 显式告诉桥去读用户真实在用的数据目录（与经典版同一份档案）
      env: { ...process.env, PYTHONIOENCODING: 'utf-8',
             TB_DATA_DIR: process.env.TTB_DATA_DIR || 'D:\\Tools\\Terminal-GUI-Toolbox\\data' },
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

/** 中文菜单：替换 Electron 默认的 File/Edit/View/Window/Help 英文菜单栏。 */
function installChineseMenu(): void {
  const send = (action: string) => {
    for (const w of BrowserWindow.getAllWindows()) w.webContents.send('ttb:menu', action)
  }
  const tpl: Electron.MenuItemConstructorOptions[] = [
    { label: '文件', submenu: [
      { label: '连接设备', click: () => send('连接') },
      { label: '主机管理', click: () => send('主机管理') },
      { type: 'separator' },
      { label: '退出', accelerator: 'Alt+F4', role: 'quit' }
    ] },
    { label: '编辑', submenu: [
      { label: '撤销', accelerator: 'Ctrl+Z', role: 'undo' },
      { label: '重做', accelerator: 'Ctrl+Y', role: 'redo' },
      { type: 'separator' },
      { label: '复制', accelerator: 'Ctrl+C', role: 'copy' },
      { label: '粘贴', accelerator: 'Ctrl+V', role: 'paste' },
      { label: '全选', accelerator: 'Ctrl+A', role: 'selectAll' }
    ] },
    { label: '视图', submenu: [
      { label: '连接页', click: () => send('连接') },
      { label: '终端页', click: () => send('终端') },
      { label: '脚本库', click: () => send('脚本库') },
      { label: '计划任务', click: () => send('计划任务') },
      { type: 'separator' },
      { label: '刷新', accelerator: 'F5', role: 'reload' },
      { label: '开发者工具', accelerator: 'F12', role: 'toggleDevTools' }
    ] },
    { label: '窗口', submenu: [
      { label: '最小化', role: 'minimize' },
      { label: '关闭', role: 'close' }
    ] },
    { label: '帮助', submenu: [
      { label: '关于', click: () => send('关于') }
    ] }
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(tpl))
}

app.whenReady().then(() => {
  installChineseMenu()
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
