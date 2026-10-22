# -*- coding: utf-8 -*-
"""运维技能（SOP）：把宝塔终端那 8 个内置技能搬过来，并支持用户自装。

为什么需要这个：模型面对「磁盘满了 / 网站 502 / 数据库慢」这类问题，如果不给
排查顺序，它就容易东一榔头西一棒子，或者一上来就改配置。技能就是一份**固定
的排查剧本**（探测 → 检查 → 诊断 → 修复 → 注意事项），塞进系统提示词里，
让自驱循环按剧本推进。

三条边界（与宝塔一致，也是我们刻意保留的）：

  1. 技能**只是文本**。目录里的脚本一律不执行——命令必须由模型按 SOP 生成，
     再由会话通道在目标机上跑，界面上看得见。
  2. 用户删掉某个内置技能后**不再塞回来**（尊重删除），靠 `.seeded` 标记控制。
  3. 装第三方技能包**只装文件、不改内容**；装完自证一次能解析出来。

frontmatter 只支持宝塔那个子集：`key: value`、`description: >` 折叠块、
`tags: [a, b]` / 缩进 `- a` 列表 / 逗号串。嵌套映射与多文档直接判坏跳过。
"""
from __future__ import annotations

import io
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from core.ai_skills_builtin import BUILTIN_SKILLS

ENTRY_NAMES = ("SKILL.md", "skill.md")
SEEDED_MARK = ".seeded"
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_ENTRY_BYTES = 64 * 1024          # 单份 SKILL.md 上限，超了判坏
AUTO_SLUG = "__auto__"               # 下拉里的「自动判断」

# 内置技能的展示顺序（先性能，再安全，最后专项），与宝塔一致
BUILTIN_ORDER = [
    "server-performance-diagnosis",
    "security-diagnosis",
    "service-cron-diagnosis",
    "network-firewall-diagnosis",
    "website-ssl-diagnosis",
    "database-diagnosis",
    "log-analysis",
    "file-ftp-diagnosis",
]


# ------------------------------------------------------------------ 数据结构
@dataclass
class Skill:
    slug: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    body: str = ""
    builtin: bool = False
    path: str = ""

    @property
    def source(self) -> str:
        return "内置" if self.builtin else "本地"

    @property
    def label(self) -> str:
        return f"{self.name} · {self.slug}"

    def keywords(self) -> list[str]:
        out = [self.name, self.slug]
        out.extend(self.tags)
        return [k for k in (str(x).strip() for x in out) if k]


# ------------------------------------------------------------------ frontmatter
class SkillError(ValueError):
    """技能文件坏了。带上事件名，方便照宝塔的日志习惯排查。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _split_block(line: str) -> tuple[str, str] | None:
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", line)
    if not m:
        return None
    return m.group(1).lower(), m.group(2)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """返回 (meta, body)。坏文件抛 SkillError（带 code）。"""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.startswith("---"):
        raise SkillError("skill.bad_frontmatter", "文件开头不是 ---")
    end = raw.find("\n---", 3)
    if end == -1:
        raise SkillError("skill.bad_frontmatter", "frontmatter 没有结束的 ---")
    head = raw[3:end].strip("\n")
    body = raw[end + 4:]
    if body.lstrip("\n").startswith("---"):
        raise SkillError("skill.bad_frontmatter", "出现第二个 --- 文档分隔")

    meta: dict = {}
    key = ""
    block: list[str] = []
    items: list[str] = []

    def flush():
        nonlocal key, block, items
        if not key:
            return
        if items:
            meta[key] = list(items)
        elif block:
            joined = " " if meta.get("__fold_" + key) else "\n"
            meta[key] = joined.join(x for x in block if x)
        key, block, items = "", [], []

    for line in head.split("\n"):
        if not line.strip():
            continue
        indented = line[:1] in (" ", "\t")
        if indented:
            if not key:
                raise SkillError("skill.bad_frontmatter", "缩进行前面没有 key")
            stripped = line.strip()
            if stripped.startswith("- "):
                items.append(stripped[2:].strip().strip("'\""))
                continue
            if meta.get(key) in (">", ">-", ">+", "|", "|-", "|+"):
                block.append(stripped)
                continue
            if stripped and _split_block(stripped):
                raise SkillError("skill.bad_frontmatter", f"不支持嵌套映射：{key}")
            block.append(stripped)
            continue

        kv = _split_block(line)
        if not kv:
            raise SkillError("skill.bad_frontmatter", f"看不懂的一行：{line[:40]}")
        flush()
        key, value = kv
        value = value.strip()
        if value in (">", ">-", ">+", "|", "|-", "|+"):
            meta[key] = value
            meta["__fold_" + key] = value.startswith(">")
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            meta[key] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
        elif value:
            meta[key] = value
        else:
            meta[key] = ""
            items = []          # 后面可能跟 - a 列表
    flush()

    for k in [k for k in meta if k.startswith("__fold_")]:
        meta.pop(k, None)

    name = str(meta.get("name") or "").strip()
    if not name:
        raise SkillError("skill.bad_frontmatter", "缺 name")

    tags: list[str] = []
    for field_name in ("tags", "keywords"):
        v = meta.get(field_name)
        if isinstance(v, list):
            tags.extend(str(x) for x in v)
        elif isinstance(v, str) and v.strip():
            tags.extend(x.strip() for x in re.split(r"[,，]", v) if x.strip())
    # 去重保序
    seen = set()
    tags = [t for t in tags if not (t in seen or seen.add(t))]

    desc = meta.get("description")
    if isinstance(desc, list):
        desc = " ".join(desc)
    return ({"name": name, "description": str(desc or "").strip(), "tags": tags},
            body.strip("\n"))


# ------------------------------------------------------------------ 技能库
class SkillStore:
    """data/skills/ 下的技能集合，同时管内置技能的首次落地。"""

    def __init__(self, skills_dir: str | Path):
        self.dir = Path(skills_dir)
        self._skills: dict[str, Skill] = {}
        self.errors: list[tuple[str, str, str]] = []   # (slug, code, msg)

    # -- 落地内置
    def seed_builtin(self) -> None:
        """首次运行写入 8 个内置技能；之后不再插手（用户删了就是删了）。"""
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            mark = self.dir / SEEDED_MARK
            if mark.is_file():
                return
            ok_all = True
            for slug, content in BUILTIN_SKILLS.items():
                target = self.dir / slug
                if (target / "SKILL.md").is_file() or (self.dir / f"{slug}.md").is_file():
                    continue
                try:
                    target.mkdir(parents=True, exist_ok=True)
                    (target / "SKILL.md").write_text(content, encoding="utf-8",
                                                     newline="\n")
                except OSError:
                    ok_all = False
            if ok_all:
                try:
                    mark.write_text("1\n", encoding="utf-8")
                except OSError:
                    pass
        except OSError:
            pass

    # -- 扫描
    def scan(self) -> list[Skill]:
        self._skills = {}
        self.errors = []
        try:
            entries = sorted(self.dir.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            entries = []

        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    self._load_dir(entry)
                elif entry.suffix.lower() == ".md":
                    self._load_flat(entry)
            except SkillError as e:
                self.errors.append((entry.stem, e.code, str(e)))
            except OSError as e:
                self.errors.append((entry.stem, "skill.io_error", str(e)))

        builtin_rank = {s: i for i, s in enumerate(BUILTIN_ORDER)}
        return sorted(
            self._skills.values(),
            key=lambda s: (builtin_rank.get(s.slug, 999) if s.builtin else 500,
                           s.name.lower()))

    def _load_dir(self, d: Path) -> None:
        slug = d.name
        if not SLUG_RE.match(slug):
            raise SkillError("skill.invalid_slug", f"目录名不合规：{slug}")
        entry = None
        for cand in ENTRY_NAMES:
            p = d / cand
            if p.is_file():
                entry = p
                break
        if entry is None:
            raise SkillError("skill.missing_entry", "目录里没有 SKILL.md")
        if entry.is_symlink():
            raise SkillError("skill.symlink_skipped", "入口是符号链接")
        size = entry.stat().st_size
        if size > MAX_ENTRY_BYTES:
            raise SkillError("skill.file_too_large", f"SKILL.md {size} 字节，超过 64KB")
        text = entry.read_text(encoding="utf-8", errors="replace")
        self._register(slug, text, str(entry))

    def _load_flat(self, p: Path) -> None:
        """旧扁平布局 skills/<slug>.md，仍可读；目录式优先。"""
        slug = p.stem
        if not SLUG_RE.match(slug):
            raise SkillError("skill.invalid_slug", f"文件名不合规：{p.name}")
        if slug in self._skills:
            raise SkillError("skill.slug_conflict", "同名目录已存在，目录式优先")
        if p.stat().st_size > MAX_ENTRY_BYTES:
            raise SkillError("skill.file_too_large", "超过 64KB")
        text = p.read_text(encoding="utf-8", errors="replace")
        self._register(slug, text, str(p))

    def _register(self, slug: str, text: str, path: str) -> None:
        meta, body = parse_frontmatter(text)
        self._skills[slug] = Skill(
            slug=slug, name=meta["name"], description=meta["description"],
            tags=meta["tags"], body=body,
            builtin=slug in BUILTIN_SKILLS, path=path)

    # -- 查询
    def all(self) -> list[Skill]:
        return self.scan()

    def get(self, slug: str) -> Skill | None:
        key = (slug or "").strip()
        if key and key not in self._skills:
            self.scan()          # 刚装进来的还没进缓存，扫一次再判
        return self._skills.get(key)

    def names(self) -> dict[str, str]:
        return {s.slug: s.name for s in (self.all() or [])}

    # -- 安装
    def install(self, src: str | Path) -> tuple[bool, str]:
        """装第三方技能：.md 单文件、<slug>/SKILL.md 目录、或这两种的 zip。"""
        p = Path(src)
        if not p.exists():
            return False, f"找不到 {p}"
        try:
            if p.is_dir():
                slug = p.name
                entry = next((p / n for n in ENTRY_NAMES if (p / n).is_file()), None)
                if entry is None:
                    return False, "目录里没有 SKILL.md"
                return self._install_dir(p, slug)
            if p.suffix.lower() == ".zip":
                return self._install_zip(p)
            if p.suffix.lower() == ".md":
                return self._install_md(p)
            return False, "只支持 .md、.zip 或含 SKILL.md 的目录"
        except (SkillError, OSError, zipfile.BadZipFile) as e:
            return False, f"{type(e).__name__}: {e}"

    def _install_md(self, p: Path) -> tuple[bool, str]:
        text = p.read_text(encoding="utf-8", errors="replace")
        meta, _ = parse_frontmatter(text)     # 先验一遍，坏了不落地
        slug = p.stem
        if not SLUG_RE.match(slug):
            slug = re.sub(r"[^A-Za-z0-9._-]", "-", slug).strip("-") or "skill"
        target = self.dir / slug / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        return True, f"已装「{meta['name']}」→ skills/{slug}/SKILL.md"

    def _install_dir(self, src: Path, slug: str) -> tuple[bool, str]:
        entry = next((src / n for n in ENTRY_NAMES if (src / n).is_file()), None)
        if entry is None:
            return False, "目录里没有 SKILL.md"
        meta, _ = parse_frontmatter(entry.read_text(encoding="utf-8", errors="replace"))
        target = self.dir / slug
        if target.exists():
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        shutil.copytree(src, target)
        return True, f"已装「{meta['name']}」→ skills/{slug}/"

    def _install_zip(self, p: Path) -> tuple[bool, str]:
        """zip 里可能是一层 <slug>/SKILL.md，也可能是多个技能各一目录。"""
        done: list[str] = []
        with zipfile.ZipFile(p) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            entries = [n for n in names
                       if os.path.basename(n) in ENTRY_NAMES]
            if not entries:
                return False, "zip 里没有 SKILL.md"
            for name in entries:
                slug = os.path.basename(os.path.dirname(name)) or p.stem
                if not SLUG_RE.match(slug):
                    return False, f"zip 里的目录名不合规：{slug}"
                prefix = os.path.dirname(name)
                target = self.dir / slug
                target.mkdir(parents=True, exist_ok=True)
                for member in names:
                    if prefix and not member.startswith(prefix + "/"):
                        continue
                    rel = member[len(prefix) + 1:] if prefix else member
                    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
                        continue
                    out = target / rel
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(z.read(member))
                meta, _ = parse_frontmatter(
                    (target / os.path.basename(name)).read_text(
                        encoding="utf-8", errors="replace"))
                done.append(f"{meta['name']}({slug})")
        return True, "已装 " + "、".join(done)


# ------------------------------------------------------------------ 自动选技能
def pick(goal: str, skills: list[Skill]) -> tuple[Skill | None, str]:
    """按关键词命中挑一个技能。命中理由照宝塔的写法回报给用户。"""
    q = (goal or "").strip().lower()
    if not q or not skills:
        return None, ""

    best: Skill | None = None
    best_score = 0
    best_hits: list[str] = []

    for s in skills:
        score = 0
        hits: list[str] = []
        if s.slug.lower() in q:
            score += 100
            hits.append(s.slug)
        if s.name and s.name.lower() in q:
            score += 40
            hits.append(s.name)
        for tag in s.tags:
            t = tag.lower()
            if len(t) >= 2 and t in q:
                score += 10
                hits.append(tag)
        # 描述里的词偶尔也命中（弱权重，只做兜底）
        if s.description:
            for word in re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z][A-Za-z0-9.+]{2,}", 
                                   s.description):
                if word.lower() in q:
                    score += 1
                    hits.append(word)
        if score > best_score:
            best, best_score, best_hits = s, score, hits

    if not best or best_score < 10:
        return None, ""
    uniq: list[str] = []
    for h in best_hits:
        if h not in uniq:
            uniq.append(h)
    return best, "命中 " + "、".join(uniq[:6])


# ------------------------------------------------------------------ 提示词片段
def render(skill: Skill, max_chars: int = 6000) -> str:
    """把技能渲染成系统提示词里的一段。"""
    body = (skill.body or "").strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…（技能正文过长，已截断）"
    return (
        f"【本轮技能】{skill.name}（{skill.slug}）\n"
        f"简介：{skill.description or '—'}\n"
        "按下面这份 SOP 的顺序推进任务：先做完「探测 / 检查」，再下结论，"
        "最后才是「修复」。SOP 只是排查顺序，**不改变输出格式**——你仍然只输出"
        "那一个 JSON 对象；每一步要跑的命令放进 commands 里，等程序把真实输出回给你"
        "再走下一步。SOP 里要求「先获授权」的动作，risk 必须标 write 或 danger。\n"
        "----- SOP 开始 -----\n"
        f"{body}\n"
        "----- SOP 结束 -----"
    )


def preferred_slug(goal: str, skills: list[Skill], wanted: str = "") -> tuple[str, str]:
    """返回 (slug, 理由)。wanted 为空或 __auto__ 时自动挑。"""
    wanted = (wanted or "").strip()
    if wanted and wanted != AUTO_SLUG:
        s = next((x for x in skills if x.slug == wanted), None)
        if s is not None:
            return s.slug, "手动指定"
        return "", f"找不到技能 {wanted}"
    s, why = pick(goal, skills)
    if s is None:
        return "", "没有匹配的技能"
    return s.slug, why


def make_store(data_dir: str | Path) -> SkillStore:
    store = SkillStore(Path(data_dir) / "skills")
    store.seed_builtin()
    store.scan()
    return store
