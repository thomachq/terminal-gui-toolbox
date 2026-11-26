# -*- coding: utf-8 -*-
"""自测：模型能力的来源与判定（不联网）。

要验的是一条链：**预设表 → 端点/探针结论 → 用户勾选** 怎么合成一个
「这一轮发不发图」的答案，以及预算与空回答提示这类边角。

为什么单独一个测试文件：`_visiontest.py` 验的是「图有没有正确塞进请求」，
能力判定错了图根本不会被塞进去 —— 两层分开测，失败时才知道是哪层。

    python _capstest.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import crypto_store                            # noqa: E402
from core import aiops                          # noqa: E402
from core import modelsync as MS                # noqa: E402

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


tmp = Path(tempfile.mkdtemp(prefix="_capstest_"))
_old_key_dir = getattr(crypto_store, "_key_dir", None)

sec("1. 预设表：按名字定能力，并说清来源")
mods, src = MS.guess_with_src("deepseek-flash")
chk(mods == ["vision"] and src == "preset", "deepseek-flash → 视觉（实测过的精确项）",
    f"{mods} / {src}")
mods, src = MS.guess_with_src("deepseek-chat")
chk(mods == [] and src == "preset", "deepseek-chat → 纯文本（显式写死，不被别的规则误判）",
    f"{mods} / {src}")
chk("vision" in MS.modality_guess("gpt-4o-2024-08-06"), "gpt-4o 家族 → 视觉")
chk("vision" in MS.modality_guess("qwen2.5-vl-7b"), "-vl 后缀 → 视觉")
chk(MS.modality_guess("qwen2.5-coder-14b") == [], "coder 系不猜视觉")
chk(MS.modality_guess("some-random-model") == [], "认不出就不猜（未知 ≠ 没有）")

sec("2. 用户覆盖表：自己的网关模型名也能钉死")
caps_file = tmp / "model_caps.json"
caps_file.write_text(json.dumps({"my-gw-vl": ["vision"], "my-gw-text": [],
                                 "weird-name": {"vision": True}}, ensure_ascii=False),
                     encoding="utf-8")
MS.set_caps_file(caps_file)
mods, src = MS.guess_with_src("my-gw-vl")
chk(mods == ["vision"] and src == "user", "覆盖表命中 → 来源算 user（比预设更可信）",
    f"{mods} / {src}")
chk(MS.guess_with_src("weird-name")[0] == ["vision"], "值写成 {\"vision\": true} 也认")
MS.set_caps_file(None)

sec("3. 端点条目：只做正向确认，绝不用来否定")
chk(MS.modalities_from_entry({"capabilities": ["completion"]}) is None,
    "只写 completion → 视为「端点没说」（不能当成没有视觉）")
chk(MS.modalities_from_entry({"architecture": {"input_modalities": ["text", "image"]}})
    == ["vision"], "Ollama 风格 architecture.input_modalities 能读")
chk(MS.modalities_from_entry({"modalities": ["vision"]}) == ["vision"],
    "OpenRouter 风格 modalities 能读")
chk(MS.modalities_from_entry({"capabilities": {"vision": True}}) == ["vision"],
    "capabilities 写成布尔字典也认")
chk(MS.modalities_from_entry({}) is None, "什么都没有 → None")
chk(MS.modalities_from_entry("just-a-string") is None, "条目是字符串 → None")

sec("4. vision_ok 真值表（这一轮到底发不发图）")


def make_cfg(name: str = "c.json") -> aiops.AIConfig:
    p = tmp / name
    p.write_text(json.dumps({
        "provider": "deepseek", "base": "https://example.com/v1",
        "model": "plain-model-x", "key_enc": "", "max_tokens": 1200,
        "models": [{"id": "m1", "provider": "deepseek", "base": "https://example.com/v1",
                    "model": "plain-model-x", "modalities": [],
                    "caps": {"thinking": {"levels": ["off", "low", "high", "max"],
                                          "default": "high"}, "tools": False}}],
        "active": "m1"}, ensure_ascii=False), encoding="utf-8")
    return aiops.AIConfig(p)


cfg = make_cfg()
chk(cfg.vision_ok() is False, "什么都没声明 → 不发（不知道就别赌，赌错是 400）")
cfg.data["modalities"] = ["vision"]
chk(cfg.vision_ok() is True, "用户勾了 → 发")
cfg = make_cfg()
cfg.set_cap_src("vision", False, "user", "我确定它不支持")
chk(cfg.vision_ok() is False, "用户显式否定 → 不发（压过自动结论）")
cfg.set_cap_src("vision", True, "preset", "按预设表")
chk(cfg.vision_ok() is True, "自动结论为真 → 也发（这就是这次补的能力）")
cfg.set_cap_src("vision", None, "user")
chk(cfg.cap_src("vision") == {}, "value=None → 结论作废")
chk(cfg.vision_ok() is False, "作废后回到「不知道」→ 不发")

# 预设表这一层：deepseek-flash 不用手勾也能发（D1「自动发图」的意图）
cfg_p = make_cfg("preset.json")
cfg_p.data["model"] = "deepseek-flash"
cfg_p.data["modalities"] = []
cfg_p.data["caps_src"] = {}
chk(cfg_p.vision_ok() is True, "预设表命中 deepseek-flash → 不勾也发")
cfg_t = make_cfg("textmodel.json")
cfg_t.data["model"] = "deepseek-chat"
cfg_t.data["modalities"] = []
cfg_t.data["caps_src"] = {}
chk(cfg_t.vision_ok() is False, "预设表写明纯文本 → 不发（也不浪费探针）")

sec("5. caps_src 要跟着模型档案走")
cfg = make_cfg()
cfg.set_cap_src("vision", True, "probe", "探针通过")
m = cfg.find_model(cfg.current_id())
chk(isinstance(m.get("caps_src"), dict) and m["caps_src"].get("vision", {}).get("value"),
    "写进当前模型档案（切模型后扁平字段被覆盖也不丢）")
chk(abs(0) == 0 and str(cfg.cap_src("vision").get("at", "")).count("-") >= 2,
    "带时间戳（出问题能查是哪天定的）", cfg.cap_src("vision").get("at"))

sec("6. 思考预算：别让 reasoning 把回答吃成空")
# chat() 会先查 Key；用临时密钥目录造一个假的，别碰真实配置
crypto_store.set_key_dir(tmp)
cfg = make_cfg()
cfg.set_key("sk-capstest-fake")
chk(bool(cfg.get_key()), "临时密钥目录能加密/解密（顺手验一把 crypto_store）")
captured = {}


def fake_post(url, payload, headers, timeout):
    captured.clear()
    captured.update(payload)
    return {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


_real_post = aiops._post_json
aiops._post_json = fake_post
try:
    cfg.data["max_tokens"] = 300
    eng = aiops.make_engine(cfg)
    eng.chat([{"role": "user", "content": "hi"}])
    chk(captured.get("max_tokens") == aiops.THINKING_MIN_BUDGET,
        "开了思考 + 用户只给 300 → 自动抬到下限",
        f"{captured.get('max_tokens')}（应该 {aiops.THINKING_MIN_BUDGET}）")

    cfg.data["max_tokens"] = 4000
    eng.chat([{"role": "user", "content": "hi"}])
    chk(captured.get("max_tokens") == 4000, "用户给得更大就照用户的", captured.get("max_tokens"))

    # 关掉思考就不该抬：否则普通模型也悄悄多花 token
    m = cfg.find_model(cfg.current_id())
    m["caps"]["thinking"] = {"levels": [], "default": ""}
    cfg.data["max_tokens"] = 300
    eng.chat([{"role": "user", "content": "hi"}])
    chk(captured.get("max_tokens") == 300, "没开思考 → 不抬高", captured.get("max_tokens"))

    # 空回答 + finish=length → 必须给一句人话
    def fake_len(url, payload, headers, timeout):
        return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 800,
                          "total_tokens": 810,
                          "completion_tokens_details": {"reasoning_tokens": 790}}}

    aiops._post_json = fake_len
    m["caps"]["thinking"] = {"levels": ["off", "high"], "default": "high"}
    text, usage = eng.chat([{"role": "user", "content": "hi"}])
    chk(text == "" and "预算" in str(usage.get("note") or ""),
        "空回答 + finish=length → 说清是预算问题", str(usage.get("note"))[:60])
finally:
    aiops._post_json = _real_post

sec("7. 懒探测：表过期也不用人管（ensure_vision）")
calls = {"n": 0}


def fake_probe(url, payload, headers, timeout):
    calls["n"] += 1
    # 回「红色」——等于承认解码了 1×1 的红图（能过「真的看到像素」这条标准）
    return {"choices": [{"message": {"content": "红色"}, "finish_reason": "stop"}],
            "usage": {}}


cfg_u = make_cfg("unknown.json")
cfg_u.set_key("sk-capstest-fake")
cfg_u.data["model"] = "某网关自建模型-xx"          # 表里没有
cfg_u.data["modalities"] = []
cfg_u.data["caps_src"] = {}
cfg_u.save()
aiops._post_json = fake_probe
try:
    ok1, why1 = cfg_u.ensure_vision()
    chk(ok1 and calls["n"] == 1, "未知模型 → 自动探一次就通过",
        f"{str(why1)[:32]} / 请求 {calls['n']} 次")
    chk(cfg_u.cap_src("vision").get("from") == "probe", "结论记成 probe（写盘，下次不用再探）")
    ok2, _w = cfg_u.ensure_vision()
    chk(ok2 and calls["n"] == 1, "第二次直接用结论，不再发请求", f"请求 {calls['n']} 次")

    cfg_x = make_cfg("textonly.json")
    cfg_x.set_key("sk-capstest-fake")
    cfg_x.data["model"] = "deepseek-chat"
    cfg_x.data["modalities"] = []
    cfg_x.data["caps_src"] = {}
    cfg_x.save()
    before = calls["n"]
    ok3, why3 = cfg_x.ensure_vision()
    chk(ok3 is False and calls["n"] == before,
        "预设表白纸黑字「纯文本」→ 不浪费探针，直接不发", str(why3)[:32])

    cfg_y = make_cfg("useroff.json")
    cfg_y.set_key("sk-capstest-fake")
    cfg_y.data["model"] = "另一个未知模型"
    cfg_y.set_cap_src("vision", False, "user", "我关掉的")
    before = calls["n"]
    ok4, why4 = cfg_y.ensure_vision()
    chk(ok4 is False and calls["n"] == before, "用户手动关掉 → 尊重，不探", str(why4)[:32])
finally:
    aiops._post_json = _real_post

sec("8. 并发写：能力结论与界面保存互不吃掉（R-016）")
p_race = tmp / "race.json"
p_race.write_text(json.dumps({
    "provider": "deepseek", "base": "https://example.com/v1",
    "model": "plain-model-x", "key_enc": "", "temperature": 0.2,
    "models": [{"id": "r1", "provider": "deepseek", "model": "plain-model-x",
                "modalities": [], "caps": {}}], "active": "r1"},
    ensure_ascii=False), encoding="utf-8")
a_worker = aiops.AIConfig(p_race)     # 假装是工作线程（懒探测）
b_ui = aiops.AIConfig(p_race)         # 假装是界面（旧快照）
a_worker.set_cap_src("vision", True, "probe", "探针通过")
b_ui.data["temperature"] = 0.9        # 界面改的是另一个字段
b_ui.save()
disk = json.loads(p_race.read_text(encoding="utf-8"))
chk((disk.get("caps_src") or {}).get("vision", {}).get("value") is True,
    "界面整份保存后，工作线程的能力结论还在",
    (disk.get("caps_src") or {}).get("vision"))
chk(disk.get("temperature") == 0.9, "界面的改动也落地了", disk.get("temperature"))
arch = (disk.get("models") or [{}])[0]
chk((arch.get("caps_src") or {}).get("vision", {}).get("value") is True,
    "档案里的那份也合并上了（切模型不丢）")

sec("9. open_config：配置与密钥目录绑在一起，解不开要说话")
cfg2, meta = aiops.open_config(tmp)          # 临时目录里没有 ai.json
chk(meta["key_ok"] is False and "ai.json" in meta["key_error"],
    "找不到 ai.json 时 key_error 说得明白", meta["key_error"][:50])
chk(str(meta["data_dir"]).endswith(str(tmp).split("\\")[-1]),
    "meta 里回的是实际用的 data 目录", meta["data_dir"])

# 收尾：把密钥目录还给原来的 —— 测试污染全局会让别的脚本解不出 Key
if _old_key_dir is not None:
    crypto_store.set_key_dir(_old_key_dir)
import shutil as _sh  # noqa: E402
_sh.rmtree(tmp, ignore_errors=True)

print(f"\n合计 {OK + BAD} 项：通过 {OK}，失败 {BAD}")
sys.exit(1 if BAD else 0)
