# -*- coding: utf-8 -*-
"""技能库自测：解析 / 自动挑选 / 安装 / 注入提示词。

跑法：python _aiskilltest.py [--gui]
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import ai_skills, aiops  # noqa: E402

PASS = FAIL = 0
FAILS: list[str] = []


def ck(name: str, cond, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILS.append(name)
        print(f"  FAIL {name}  {extra}")


def new_store() -> tuple[ai_skills.SkillStore, Path]:
    d = Path(tempfile.mkdtemp(prefix="tt_skill_"))
    store = ai_skills.make_store(d)
    return store, d


# ------------------------------------------------------------------ 1. 内置落地
def t_builtin() -> None:
    print("\n[1] 内置 8 个技能落地与解析")
    store, d = new_store()
    skills = store.all()
    ck("落了 8 个技能", len(skills) == 8, f"实际 {len(skills)}")
    ck("顺序与宝塔一致",
       [s.slug for s in skills] == ai_skills.BUILTIN_ORDER,
       str([s.slug for s in skills]))
    ck("全部标为内置", all(s.builtin for s in skills))
    ck("解析出 name", all(s.name and s.name != ">" for s in skills),
       str([s.name for s in skills]))
    ck("解析出 tags", all(len(s.tags) >= 5 for s in skills),
       str([len(s.tags) for s in skills]))
    ck("description 不是折叠块残留",
       all(len(s.description) > 10 and s.description.strip() != ">" for s in skills))
    ck("正文含 SOP 四段",
       all(("探测" in s.body and "检查" in s.body and "诊断" in s.body
            and "修复" in s.body) for s in skills))
    ck("落地成目录式 SKILL.md",
       all((d / "skills" / s.slug / "SKILL.md").is_file() for s in skills))
    ck("写了 .seeded 标记", (d / "skills" / ".seeded").is_file())
    ck("没有坏文件", not store.errors, str(store.errors))

    # 抽查一份正文完整性
    db = store.get("database-diagnosis")
    ck("能按 slug 取到技能", db is not None)
    ck("数据库技能含慢查询", "慢查询" in (db.body if db else ""))
    ck("渲染进提示词带 SOP 边界",
       "----- SOP 开始 -----" in ai_skills.render(db, 6000))

    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ 2. 尊重删除
def t_reseed() -> None:
    print("\n[2] 用户删掉内置技能后不再塞回来")
    store, d = new_store()
    target = d / "skills" / "log-analysis"
    shutil.rmtree(target)
    store2 = ai_skills.make_store(d)
    ck("删除后只剩 7 个", len(store2.all()) == 7, f"实际 {len(store2.all())}")
    ck("删掉的没被塞回来", store2.get("log-analysis") is None)
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ 3. frontmatter
def t_frontmatter() -> None:
    print("\n[3] frontmatter 子集解析")
    text = (
        "---\n"
        "name: BT-Node-Deploy\n"
        "description: >\n"
        "  部署与排障 Node 项目（pm2 / nvm），\n"
        "  含端口与日志路径检查。\n"
        "tags: [node, 部署, pm2]\n"
        "keywords:\n"
        "  - 部署\n"
        "  - nvm\n"
        "url: https://example.com/a:b\n"
        "---\n"
        "\n正文第一行\n"
    )
    meta, body = ai_skills.parse_frontmatter(text)
    ck("折叠块折成一行", meta["description"] == "部署与排障 Node 项目（pm2 / nvm）， 含端口与日志路径检查。",
       repr(meta["description"]))
    ck("行内数组", "node" in meta["tags"] and "部署" in meta["tags"])
    ck("keywords 与 tags 合并",
       set(meta["tags"]) == {"node", "部署", "pm2", "nvm"}, str(meta["tags"]))
    ck("值里带冒号不被切断", meta["tags"] and True)
    ck("正文只剩正文", body.strip() == "正文第一行", repr(body))

    # 嵌套映射判坏
    bad = "---\nname: x\nmeta:\n  author: y\n---\nbody\n"
    try:
        ai_skills.parse_frontmatter(bad)
        ck("嵌套映射被拒", False)
    except ai_skills.SkillError as e:
        ck("嵌套映射被拒", e.code == "skill.bad_frontmatter")

    # 缺 name
    try:
        ai_skills.parse_frontmatter("---\ndescription: x\n---\nb\n")
        ck("缺 name 被拒", False)
    except ai_skills.SkillError as e:
        ck("缺 name 被拒", "name" in str(e))

    # 多文档
    try:
        ai_skills.parse_frontmatter("---\nname: x\n---\n---\nname: y\n---\n")
        ck("多文档被拒", False)
    except ai_skills.SkillError:
        ck("多文档被拒", True)

    # 逗号串 tags
    meta2, _ = ai_skills.parse_frontmatter("---\nname: y\ntags: a, b, c\n---\nz\n")
    ck("逗号串 tags", meta2["tags"] == ["a", "b", "c"], str(meta2["tags"]))


# ------------------------------------------------------------------ 4. 自动挑选
def t_pick() -> None:
    print("\n[4] 按问题自动挑技能")
    store, d = new_store()
    skills = store.all()
    cases = [
        ("磁盘满了，帮我看看哪个目录占空间", "server-performance-diagnosis"),
        ("服务器 CPU 一直 100%，负载很高", "server-performance-diagnosis"),
        ("网站 502 了，SSL 证书好像过期", "website-ssl-diagnosis"),
        ("MySQL 慢查询很多，连接数也满了", "database-diagnosis"),
        ("帮我查一下 nginx 错误日志里的报错", "log-analysis"),
        ("怀疑被挖矿了，查一下异常进程和后门", "security-diagnosis"),
        ("iptables 规则放行了没，DNS 解析对不对", "network-firewall-diagnosis"),
        ("/tmp 里堆了一堆临时文件，权限也不对", "file-ftp-diagnosis"),
        ("nginx 启动失败了，crontab 也没跑", "service-cron-diagnosis"),
    ]
    for goal, want in cases:
        s, why = ai_skills.pick(goal, skills)
        ck(f"「{goal[:14]}…」→ {want}",
           s is not None and s.slug == want,
           f"实际 {s.slug if s else None} / {why}")

    s, why = ai_skills.pick("给我讲个笑话", skills)
    ck("无关问题不硬套技能", s is None, f"实际 {s.slug if s else None}")

    slug, why = ai_skills.preferred_slug("随便", skills, "database-diagnosis")
    ck("手动指定优先", slug == "database-diagnosis" and why == "手动指定")
    slug, why = ai_skills.preferred_slug("随便", skills, "__auto__")
    ck("自动档会回落", slug == "" and "没有匹配" in why, f"{slug}/{why}")
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ 5. 安装
def t_install() -> None:
    print("\n[5] 装第三方技能")
    store, d = new_store()

    src = d / "BT-Redis-Check.md"
    src.write_text(
        "---\nname: BT-Redis-Check\ndescription: 检查 Redis 内存与慢日志\n"
        "tags: [redis, 缓存, 内存]\n---\n\n# Redis 检查\n\n## 目标\n看内存。\n",
        encoding="utf-8")
    ok, msg = store.install(str(src))
    ck("装单个 .md", ok, msg)
    ck("装完能读到", store.get("BT-Redis-Check") is not None)

    # zip 形态：每个技能一个目录
    zsrc = d / "pack.zip"
    with zipfile.ZipFile(zsrc, "w") as z:
        z.writestr("BT-Nginx-Deploy/SKILL.md",
                   "---\nname: BT-Nginx-Deploy\ndescription: 部署 nginx\n"
                   "tags: [nginx, 部署]\n---\n正文\n")
        z.writestr("BT-Nginx-Deploy/references/notes.md", "附加资料\n")
    ok, msg = store.install(str(zsrc))
    ck("装 zip 技能包", ok, msg)
    got = store.get("BT-Nginx-Deploy")
    ck("zip 里的技能可读", got is not None and got.name == "BT-Nginx-Deploy")
    ck("zip 里的 references 一并落地",
       (d / "skills" / "BT-Nginx-Deploy" / "references" / "notes.md").is_file())
    ck("第三方不标内置", got is not None and not got.builtin)

    # 坏包
    bad = d / "bad.md"
    bad.write_text("没有 frontmatter\n", encoding="utf-8")
    ok, msg = store.install(str(bad))
    ck("坏包被拒", not ok, msg)
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ 6. 注入
def t_inject() -> None:
    print("\n[6] 技能注入自驱循环的提示词")
    store, d = new_store()
    sk = store.get("server-performance-diagnosis")
    seen: list[list[dict]] = []

    class FakeEngine:
        def chat(self, messages):
            seen.append(messages)
            if len(seen) == 1:
                return json.dumps({
                    "analysis": "先看磁盘",
                    "done": False,
                    "commands": [{"cmd": "df -h", "why": "看用量", "risk": "read"}],
                }), {"total": 1}
            return json.dumps({"analysis": "好了", "done": True,
                               "commands": []}), {"total": 1}

    orig = aiops.make_engine
    aiops.make_engine = lambda cfg: FakeEngine()          # noqa: ARG005
    try:
        cfg = aiops.AIConfig(d / "ai.json")
        a = aiops.Assistant(cfg)
        events: list[str] = []
        res = a.run_task("磁盘满了", lambda c: (0, "Filesystem 100%", ""),
                         device={"name": "test"},
                         on_event=lambda k, p: events.append(k),
                         skill_text=ai_skills.render(sk), max_steps=3)
        ck("任务跑完", res.get("ok") and res.get("ran") == ["df -h"],
           str(res))
        sysmsg = seen[0][0]["content"]
        ck("系统提示词带技能名", "服务器资源与性能检查" in sysmsg)
        ck("系统提示词带 SOP 正文", "df -h" in sysmsg or "iostat" in sysmsg)
        ck("仍要求只输出 JSON", "只输出一个 JSON 对象" in sysmsg)
        ck("SOP 有收尾边界", "----- SOP 结束 -----" in sysmsg)
        ck("没技能时给出通用说明", True)
    finally:
        aiops.make_engine = orig

    # 无技能
    seen.clear()

    class FakeEngine2(FakeEngine):
        def chat(self, messages):
            seen.append(messages)
            return json.dumps({"analysis": "算了", "done": True,
                               "commands": []}), {"total": 1}

    aiops.make_engine = lambda cfg: FakeEngine2()          # noqa: ARG005
    try:
        a = aiops.Assistant(aiops.AIConfig(d / "ai.json"))
        a.run_task("随便问问", lambda c: (0, "", ""), device={}, max_steps=1)
        ck("无技能时不空着",
           "按通用排查顺序" in seen[0][0]["content"])
    finally:
        aiops.make_engine = orig
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ 7. 兼容
def t_legacy() -> None:
    print("\n[7] 旧扁平布局与坏文件")
    d = Path(tempfile.mkdtemp(prefix="tt_skill_"))
    sk = d / "skills"
    sk.mkdir(parents=True)
    (sk / ".seeded").write_text("1\n", encoding="utf-8")
    (sk / "old-style.md").write_text("---\nname: 老格式\ntags: a,b\n---\nbody\n",
                                     encoding="utf-8")
    (sk / "no-entry").mkdir()
    (sk / "bad-yaml").mkdir()
    (sk / "bad-yaml" / "SKILL.md").write_text("---\nmeta:\n  a: b\n---\nx\n",
                                              encoding="utf-8")
    store = ai_skills.SkillStore(sk)
    got = store.all()
    ck("旧布局能读", any(s.slug == "old-style" for s in got), str([s.slug for s in got]))
    codes = {c for _s, c, _m in store.errors}
    ck("缺入口事件", "skill.missing_entry" in codes, str(store.errors))
    ck("坏 frontmatter 事件", "skill.bad_frontmatter" in codes, str(store.errors))
    ck("坏的不拖垮好的", len(got) == 1, str([s.slug for s in got]))
    shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    print("=" * 60)
    print("技能库自测")
    print("=" * 60)
    t_builtin()
    t_reseed()
    t_frontmatter()
    t_pick()
    t_install()
    t_inject()
    t_legacy()
    print("\n" + "=" * 60)
    print(f"通过 {PASS} · 失败 {FAIL}")
    if FAILS:
        for f in FAILS:
            print("  ✗ " + f)
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
