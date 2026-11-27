# -*- coding: utf-8 -*-
"""终端 Tab 补全的两块判断逻辑（R-009）。

为什么只测这两块：Tab 的绑定必须 `return "break"`，没有真窗口就按不下去；
但「这次该补路径还是补命令」「`ls` 的输出怎么变成候选」这两步是纯函数，
而且正是最容易写错的地方 —— 补出个裸文件名把 `/var/lo` 变成 `log/`，
或者把 `.bashrc` 削成 `bashrc`，都得靠这里拦住。

    python _termtabtest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from ui import terminal as T                   # noqa: E402

OK = 0
BAD = 0


def chk(cond, msg, extra=None):
    global OK, BAD
    if cond:
        OK += 1
        print(f"  [ok] {msg}" + (f"  —— {extra}" if extra not in (None, "") else ""))
    else:
        BAD += 1
        print(f"  [NG] {msg}" + (f"  —— {extra}" if extra not in (None, "") else ""))


def sec(t):
    print(f"\n-- {t}")


sec("1. 该补命令还是补路径")
chk(T.completion_kind("")[0] == "cmd", "空行 → 补命令名")
chk(T.completion_kind("cl")[0] == "cmd", "行首半个命令 → 补命令名")
chk(T.completion_kind("cd ")[0] == "path", "`cd ` 后面 → 补路径",
    T.completion_kind("cd ")[:2])
chk(T.completion_kind("cd /va")[0] == "path", "`cd /va` → 补路径")
chk(T.completion_kind("cat ./")[0] == "path", "`cat ./` → 补路径")
chk(T.completion_kind("ls -l")[0] == "none", "`ls -l` 是开关参数，不补")
chk(T.completion_kind("systemctl ")[0] == "cmd",
    "不认识的命令后面 → 退回命令池（不瞎问远端）")
chk(T.completion_kind("python /opt/ap")[0] == "path",
    "python 后面带斜杠 → 补路径")
chk(T.completion_kind("cd /va/lo")[2] == "/va/lo", "行尾词原样带回来")

sec("2. ls 输出 → 整词候选")
# `ls -1p -- /var/lo` 这种：输出带目录前缀
items = T.parse_ls_listing("/var/lo", "/var/log/\n/var/lock/\n")
chk(items == ["/var/log/", "/var/lock/"], "带目录前缀的候选原样保留",
    items)
items2 = T.parse_ls_listing("/var/log/", "/var/log/syslog\n/var/log/auth.log\n")
chk(len(items2) == 2 and all(x.startswith("/var/log/") for x in items2),
    "换到下一层目录同样带前缀", items2)
chk(T.parse_ls_listing("/var/log/", "/var/log/syslog\n")[0] == "/var/log/syslog",
    "唯一候选就是它")

sec("3. `./` 与隐藏文件的坑")
items3 = T.parse_ls_listing("", "./a.txt\n./.bashrc\n./b.txt\n")
chk("./.bashrc" in items3 or ".bashrc" in items3,
    "隐藏文件`点`不能被削掉（lstrip(\"./\") 会把它削成 bashrc）", items3)
chk(len(items3) == 3, "三个都给出来（两个普通 + 一个隐藏）", items3)

sec("4. 空输出 / 脏输出不伪造")
chk(T.parse_ls_listing("/x", "") == [], "查不到 → 空候选（调用方因此不动输入框）")
chk(T.parse_ls_listing("/x", "\n\n") == [], "只有空行 → 空候选")
chk(T.parse_ls_listing("/var/log/", "/var/log/:\n/var/log/syslog\n") ==
    ["/var/log/syslog"], "跳过 ls 的「/var/log/:」这种目录头行",
    T.parse_ls_listing("/var/log/", "/var/log/:\n/var/log/syslog\n"))
chk(T.parse_ls_listing("/nomatch", "/var/log/syslog\n") == [],
    "前缀不匹配 → 空（不乱补）")

sec("5. 静态检查：别把「异步 + 不伪造」这两条弄丢")
src = (HERE / "ui" / "terminal.py").read_text(encoding="utf-8")
chk("term_capture(" in src, "路径补全走同步抓取接口")
chk("PATH_CACHE_TTL" in src and "_path_cache" in src,
    "有缓存（连按 Tab 不会连发 ls）")
chk("if not items:\n            return" in src.replace("\r\n", "\n"),
    "空候选直接返回，不动输入框（不伪造）")
chk("if self._get_input() != snapshot:" in src,
    "回填前比对输入框，用户改了就不抢")

sec("6. Windows 本机：反斜杠路径与 PowerShell 命令")
chk(T.split_dir_prefix("C:\\Us") == ("C:\\", "Us"), "认反斜杠，盘符前缀不被吃掉",
    T.split_dir_prefix("C:\\Us"))
chk(T.split_dir_prefix("/var/lo") == ("/var/", "lo"), "斜杠照旧")
chk(T.split_dir_prefix("plain") == ("", "plain"), "没有分隔符 → 全算名字前缀")
_win = T.path_list_cmd("windows", "C:\\Us")
chk("Get-ChildItem" in _win and "'C:\\Us'" in _win.replace("''", "'"),
    "Windows 用 Get-ChildItem（PowerShell 没有 `ls -1p`）", _win[:60])
_unix = T.path_list_cmd("unix", "/var/lo")
chk("ls -1p" in _unix, "Linux 用 ls -1p", _unix)

print(f"\n合计 {OK + BAD} 项：通过 {OK}，失败 {BAD}")
sys.exit(1 if BAD else 0)
