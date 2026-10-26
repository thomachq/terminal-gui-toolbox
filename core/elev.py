# -*- coding: utf-8 -*-
"""Windows 一次性 UAC 提权：点一次「是」，**应用不用重启**。

问题：Windows 没有 sudo。要让某条命令以管理员身份跑，传统做法是整个进程
以管理员重启 —— 用户点了 UAC「是」，窗口闪一下没了，之前连着的会话、开着
的终端、滚到一半的日志全部丢。2026-10-26 用户原话：

    勾选管理员权限后，只要点 UAC 通过，而不需要重新启动应用

做法：界面照旧以普通权限跑；点 UAC 之后另外拉起一个**同名的自己**
（`TerminalToolbox.exe --elev-serve <目录>`）以管理员身份常驻。它不开窗口，
只从目录里领命令、把 rc/输出写回目录。主程序要提权就把命令丢进去等结果。

为什么用「目录里放文件」而不是命名管道 / socket：
  · 提权进程与主程序都是同一个用户，文件系统一定通；管道的 DACL 一个没配
    对就是「拒绝访问」，而这类错误会伪装成「UAC 没通过」，最难查。
  · 出问题能直接看目录里剩下的文件，日志里也放得下。
  · 不需要主程序常驻一个服务端线程。
  代价是每条命令多几次磁盘 IO（实测 3~8ms），对这个用途无所谓。

目录带随机后缀（`data/elev/h<时间>-<8位十六进制>`）：同用户下的其它进程
要是猜到路径，就能往队列里塞命令变相提权，随机名让它无从下手。

助手会盯着主程序 PID：主程序被关掉或崩了，它自己退出 —— 否则一个管理员
进程在后台常驻，用户翻任务管理器时会被吓到。
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_IS_WIN = sys.platform == "win32"

# 轮询间隔：20ms 看着很勤，但只是 stat 一个文件，实测 CPU 占用可以忽略；
# 再大（比如 100ms）就会把「一条命令 3ms」的延迟拉到 100ms，终端敲命令会顿。
_POLL = 0.02
# 起来之后等 ready.json 的上限。onefile 首次要解压 20MB（Defender 还要扫），
# 冷启动实测 2~6 秒；留到 90 秒是为了别在慢机器上误判成「UAC 没通过」。
_START_TIMEOUT = 90.0
_SYNCHRONIZE = 0x00100000

_k32 = None
if _IS_WIN:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]


class ElevError(RuntimeError):
    """提权通道不可用（没启动 / 中途死了 / 超时）。"""


# ------------------------------------------------------------------ 小工具
def data_dir() -> Path:
    """和 app.py 同一套规则定位 data/，但不 import app（会循环依赖）。

    `TB_DATA_DIR` 是自测用的重定向，必须认，否则测试会往真实 data 里写。
    """
    env = os.environ.get("TB_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parent.parent / "data"


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _pid_alive(pid: int) -> bool:
    """进程还在不在。**只有 SYNCHRONIZE 权限**。

    高完整性级别（提权）的进程，普通权限打不开 PROCESS_QUERY_INFORMATION，
    但 SYNCHRONIZE 不受完整性级别限制 —— 换成别的权限位就会一律"打不开"，
    于是「助手还活着」被误判成"死了"，每条命令都报「提权通道已断开」。
    """
    if not _IS_WIN or not pid:
        return False
    h = _k32.OpenProcess(_SYNCHRONIZE, False, int(pid))
    if not h:
        # 拒绝访问 = 进程在、只是权限不够；其余（多半是 87 参数错）= 已退出
        return ctypes.get_last_error() == 5
    try:
        return _k32.WaitForSingleObject(h, 0) != 0     # 258 = WAIT_TIMEOUT
    finally:
        _k32.CloseHandle(h)


def _write_json(path: Path, obj) -> None:
    """先写 .tmp 再改名 —— 对面按目录扫文件，读到半个 JSON 就整条命令丢了。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, keep: bool = False, retries: int = 4):
    """读一个 JSON 文件。读不到 → None；读到了 → 内容（默认读走就删）。

    解析失败**先重试几次再删**：
      · 不删 → 助手每轮都会再看到它，满速空转（100% CPU）
      · 立刻删 → 万一只是短暂读不全（杀软锁着、写入方还在落盘），
        这条请求/结果就永久丢了，客户端只能干等到超时
    两个坑都踩过，所以折中成「重试 4 次（约 30ms）仍解析不了才删」。
    """
    for i in range(max(1, retries)):
        try:
            txt = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            time.sleep(0.01)
            continue
        try:
            obj = json.loads(txt)
        except ValueError:
            if i + 1 < max(1, retries):
                time.sleep(0.01)
                continue
            if not keep:
                try:
                    path.unlink()
                except OSError:
                    pass
            return None
        if not keep:
            try:
                path.unlink()
            except OSError:
                pass
        return obj
    return None


def _note(d: Path, text: str) -> None:
    """助手侧的流水账（`<会话目录>/serve.log`）。

    只记「第几条命令开始/结束」，一条命令两行，对性能没有影响。
    出问题时这是**唯一**能看到「助手到底跑没跑、跑了多久」的地方 ——
    客户端的 124 只说明没等到结果，分辨不出是助手卡了还是命令卡了。
    """
    try:
        with (d / "serve.log").open("a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {text}\n")
    except OSError:
        pass


def _shell_err(rc: int) -> str:
    """ShellExecuteW 的返回值翻成人话。

    最常见的两个：5 = 用户在 UAC 上点了「否」，1223 = 被策略拦了（企业机器
    关掉提权）。混在一起说「提权失败」等于什么都没说。
    """
    return {
        0: "内存不足，提权进程没起来",
        2: "找不到程序文件",
        3: "找不到程序路径",
        5: "已取消管理员授权（UAC 上点了「否」）",
        26: "共享冲突，提权进程没起来",
        27: "文件关联不完整，提权进程没起来",
        28: "DDE 超时，提权进程没起来",
        29: "DDE 失败，提权进程没起来",
        30: "DDE 忙，提权进程没起来",
        31: "没有关联的程序",
        32: "动态链接库找不到",
        1223: "管理员授权被系统策略拦下（这台机器禁用了提权）",
    }.get(int(rc), f"提权进程启动失败（ShellExecute 返回 {rc}）")


def _is_admin() -> bool:
    if not _IS_WIN:
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _argv_for(cmd: str, shell: str = "") -> list[str]:
    """和 LocalSession.argv_for 同一套壳选择，直接复用它的实现。"""
    from core.session import windows_argv
    if shell == "cmd":
        return ["cmd", "/c", cmd]
    if shell == "powershell":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd]
    return windows_argv(cmd)


# ------------------------------------------------------------------ 助手侧
def _kill_tree(pid: int) -> None:
    """杀整棵进程树。

    只 `Popen.kill()` 杀顶层是不够的：powershell 底下再起个程序，孙进程会
    继承 stdout 管道，`communicate()` 永远等不到 EOF —— 一条卡死的命令就能把
    整个提权通道拖死（实测：超时那条命令之后，后面的命令全部 124）。
    """
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))],
                       capture_output=True, timeout=20,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:  # noqa: BLE001
        pass


def _run_job(job: dict) -> dict:
    from core.session import decode_bytes
    cmd = str(job.get("cmd") or "")
    tmo = float(job.get("timeout") or 60)
    stdin_text = str(job.get("stdin") or "")
    t0 = time.time()
    rc, out_b, err_b, extra = 0, b"", b"", ""
    p = None
    try:
        p = subprocess.Popen(
            _argv_for(cmd, str(job.get("shell") or "")),
            stdin=(subprocess.PIPE if stdin_text else subprocess.DEVNULL),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            out_b, err_b = p.communicate(
                input=(stdin_text.encode("utf-8") if stdin_text else None),
                timeout=tmo)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            _kill_tree(p.pid)
            try:
                out_b, err_b = p.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out_b, err_b = b"", b""
            rc = 124
            extra = f"命令超时（{tmo:.0f}s），已终止"
    except FileNotFoundError as e:
        rc, extra = 127, str(e)
    except OSError as e:
        rc, extra = 126, str(e)
    except Exception as e:  # noqa: BLE001
        rc, extra = 125, f"{type(e).__name__}: {e}"
    out = decode_bytes(out_b)
    err = decode_bytes(err_b) or extra
    return {"id": job.get("id"), "rc": rc, "out": out, "err": err,
            "ms": int((time.time() - t0) * 1000)}


def serve(dirpath: str, parent_pid: int = 0) -> int:
    """提权助手主循环。返回码不给人看，只进日志。

    `parent_pid` 是主程序 PID。它一没，助手立刻退出；同时也认 `stop.flag`
    （正常关闭走这条，不用等进程消失）。
    """
    d = Path(str(dirpath or ""))
    if not d.is_dir():
        return 2
    admin = _is_admin()
    _write_json(d / "ready.json", {
        "pid": os.getpid(), "admin": admin,
        "user": os.environ.get("USERNAME", ""), "t": time.time()})
    if not admin:
        # 走到这里说明 UAC 那句「是」没生效（策略放行但没提权）。
        # 不带管理员权限的助手比没有更坏：命令照样失败，用户还以为提权成功了。
        _write_json(d / "error.json", {"error": "提权助手没有拿到管理员权限"})
        return 3
    while True:
        try:
            if (d / "stop.flag").exists():
                break
            if parent_pid and not _pid_alive(parent_pid):
                break
            done = False
            for req in sorted(d.glob("req-*.json")):
                done = True
                job = _read_json(req)
                if not job:
                    continue
                _note(d, f"job {job.get('id')} start")
                res = _run_job(job)
                try:
                    _write_json(d / f"res-{job.get('id')}.json", res)
                    _note(d, f"job {job.get('id')} end rc={res.get('rc')} "
                             f"ms={res.get('ms')}")
                except Exception as e:  # noqa: BLE001
                    _note(d, f"job {job.get('id')} WRITE FAIL "
                             f"{type(e).__name__}: {e}")
            if not done:
                time.sleep(_POLL)
        except KeyboardInterrupt:
            break
        except Exception:  # noqa: BLE001
            # 单条命令出意外不能把整条通道带走 —— 睡一下继续等
            time.sleep(0.1)
    return 0


# ------------------------------------------------------------------ 主程序侧
class Helper:
    """提权助手的主程序侧句柄（一个进程只用一个）。"""

    def __init__(self) -> None:
        self.dir: Path | None = None
        self.pid = 0
        self.last_error = ""
        self._seq = 0
        self._lock = threading.Lock()

    # ---------------- 状态
    @property
    def alive(self) -> bool:
        return bool(self.dir) and _pid_alive(self.pid)

    def describe(self) -> str:
        return f"提权助手 pid={self.pid}" if self.alive else "未提权"

    # ---------------- 启停
    def _new_dir(self) -> Path:
        import secrets
        root = data_dir() / "elev"
        root.mkdir(parents=True, exist_ok=True)
        # 清掉上一轮的残留（助手已经退出的目录），别越攒越多
        for old in root.glob("h*"):
            if old.is_dir() and not _pid_alive(_pid_in(old)):
                try:
                    for f in old.iterdir():
                        f.unlink()
                    old.rmdir()
                except OSError:
                    pass
        tag = f"h{int(time.time())}-{secrets.token_hex(4)}"
        return root / tag

    def start(self, timeout: float = _START_TIMEOUT) -> bool:
        """拉起提权助手。**会弹 UAC**，成功后本进程权限不变、命令改走助手。

        返回 False 时 `last_error` 里是给用户看的一句话（区分「点了否」、
        「被策略拦了」和「起来了但不是管理员」）。
        """
        self.last_error = ""
        if self.alive:
            return True
        if not _IS_WIN:
            self.last_error = "只有 Windows 需要 UAC 提权"
            return False
        if _is_admin():
            # 本来就是管理员：不必再起助手，直接让调用方走直连
            self.last_error = ""
            return True
        d = self._new_dir()
        exe = sys.executable
        if getattr(sys, "frozen", False):
            args = ["--elev-serve", str(d), "--parent", str(os.getpid())]
        else:
            args = [str(Path(sys.argv[0]).resolve()), "--elev-serve",
                    str(d), "--parent", str(os.getpid())]
        params = subprocess.list2cmdline(args)
        try:
            rc = int(ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, params, str(_app_dir()), 0))
        except Exception as e:  # noqa: BLE001
            self.last_error = f"调不起 UAC：{type(e).__name__}: {e}"
            return False
        if rc <= 32:
            self.last_error = _shell_err(rc)
            self._drop(d)
            return False
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            err = _read_json(d / "error.json")
            if err:
                self.last_error = str(err.get("error") or "提权助手启动失败")
                self._drop(d)
                return False
            rd = _read_json(d / "ready.json", keep=True)
            if rd:
                if not rd.get("admin"):
                    self.last_error = "提权助手起来后仍不是管理员（UAC 被放行但未提权）"
                    self._drop(d)
                    return False
                self.dir, self.pid = d, int(rd.get("pid") or 0)
                if not self.pid or not _pid_alive(self.pid):
                    self.last_error = "提权助手启动后立刻退出了"
                    self._drop(d)
                    return False
                return True
            time.sleep(0.05)
        self.last_error = (f"提权助手 {timeout:.0f} 秒内没就绪"
                           "（可能被杀毒软件拦了）")
        self._drop(d)
        return False

    def _drop(self, d: Path) -> None:
        try:
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
        except OSError:
            pass

    def stop(self) -> None:
        d, pid = self.dir, self.pid
        self.dir, self.pid = None, 0
        if not d:
            return
        try:
            (d / "stop.flag").write_text("1", encoding="utf-8")
        except OSError:
            pass
        for _ in range(60):
            if not _pid_alive(pid):
                break
            time.sleep(0.05)
        self._drop(d)

    # ---------------- 执行
    def exec(self, cmd: str, timeout: float = 60.0,
             shell: str = "", stdin_text: str = "") -> tuple[int, str, str]:
        """以管理员身份跑一条命令。

        串行化：助手只有一个命令行，两个后台任务同时丢命令会互相等，
        但结果按 id 对齐，不会串。
        """
        if not self.alive:
            raise ElevError(self.last_error or "提权通道没启动")
        with self._lock:
            self._seq += 1
            rid = self._seq
            d = self.dir
            try:
                _write_json(d / f"req-{rid}.json", {
                    "id": rid, "cmd": cmd, "timeout": float(timeout),
                    "shell": shell or "", "stdin": stdin_text or ""})
            except OSError as e:
                raise ElevError(f"命令发不出去：{e}") from e
            deadline = time.time() + float(timeout) + 10.0
            res = None
            tries = 0
            last = ""
            while time.time() < deadline:
                tries += 1
                p = d / f"res-{rid}.json"
                try:
                    res = json.loads(p.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    pass
                except (OSError, ValueError) as e:
                    # 读到了但解析不了：绝对不能删（删了这条就永远没结果），
                    # 只记下原因继续等 —— 可能是写入方还没落完/杀软短暂锁着。
                    last = f"{type(e).__name__}: {e}"
                else:
                    try:
                        p.unlink()     # 结果读走就删，不在目录里堆积
                    except OSError:
                        pass
                    break
                if not _pid_alive(self.pid):
                    # 助手没了：把排队那条也撤掉，免得下次启动时被重放
                    try:
                        (d / f"req-{rid}.json").unlink()
                    except OSError:
                        pass
                    raise ElevError("提权助手已退出（可能被安全软件结束）")
                time.sleep(_POLL)
            if not res:
                try:
                    (d / f"req-{rid}.json").unlink()
                except OSError:
                    pass
                _note(d, f"client {rid} timeout tries={tries} last={last!r}")
                return 124, "", f"提权命令超时（{timeout:.0f}s 内没返回）"
            return (int(res.get("rc") or 0), str(res.get("out") or ""),
                    str(res.get("err") or ""))


def _pid_in(d: Path) -> int:
    rd = _read_json(d / "ready.json", keep=True) or {}
    try:
        return int(rd.get("pid") or 0)
    except (TypeError, ValueError):
        return 0


_h: Helper | None = None


def helper() -> Helper:
    """全局唯一助手句柄（UAC 一个应用只该问一次）。"""
    global _h
    if _h is None:
        _h = Helper()
    return _h


def current() -> Helper | None:
    """已经起来的助手；没起来返回 None（调用方据此决定报错还是走直连）。"""
    return _h if (_h is not None and _h.alive) else None


def reset() -> None:
    """丢掉全局句柄（测试用；正常退出走 shutdown）。"""
    global _h
    if _h is not None:
        _h.stop()
    _h = None


def shutdown() -> None:
    global _h
    if _h is not None:
        _h.stop()
        _h = None


__all__ = ["ElevError", "Helper", "helper", "current", "reset", "shutdown",
           "serve", "data_dir", "shell_err", "_pid_alive"]

# 对外别名：`_shell_err` 是内部写法，主程序报错要用它把 ShellExecute 的
# 数字翻成人话，「点了否」和「被策略拦」不能混成一句「提权失败」。
shell_err = _shell_err
