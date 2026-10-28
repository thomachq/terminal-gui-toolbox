#!/usr/bin/env python3
"""清理飞牛 tb-selftest.local 测试证书，并核对与备份是否一致"""
import json, os, shutil, sys, glob

CONF = "/usr/trim/etc/network_cert_all.conf"
DOM = "tb-selftest.local"
SS = "/usr/trim/var/trim_connect/ssls"

# 找最新备份
baks = sorted(glob.glob("/root/certbak/network_cert_all.conf.*"))
print("=== 备份文件 ===")
for b in baks[-3:]:
    print("  ", b, os.path.getsize(b), "字节")

print("=== 当前条目 ===")
items = json.load(open(CONF, encoding="utf-8"))
print("  ", [(i["domain"], i["used"]) for i in items])

# 只删我们加的那条
keep = [i for i in items if i["domain"] != DOM]
removed = len(items) - len(keep)
print(f"=== 移除 {removed} 条 ===")

out = json.dumps(keep, ensure_ascii=False, separators=(",", ":"))
tmp = CONF + ".tbtmp"
open(tmp, "w", encoding="utf-8").write(out)
os.chmod(tmp, 0o644)
os.replace(tmp, CONF)

print("=== 回读 ===")
back = json.loads(open(CONF, encoding="utf-8").read())
print("  ", [(i["domain"], i["used"]) for i in back])

# 与备份比对（用最新备份，但只在这个备份是"干净"的时候才比）
if baks:
    newest = baks[-1]
    raw_now = open(CONF, "rb").read()
    raw_bak = open(newest, "rb").read()
    print(f"=== 与备份 {os.path.basename(newest)} 比对 ===")
    print("  当前:", len(raw_now), "字节 | 备份:", len(raw_bak), "字节 |", "完全一致 ✓" if raw_now == raw_bak else "有差异")

# 删测试证书目录
d = os.path.join(SS, DOM)
if os.path.isdir(d):
    files = []
    for root, _, fs in os.walk(d):
        for f in fs:
            files.append(os.path.join(root, f))
    print(f"=== 删除测试目录 {d} （{len(files)} 个文件）===")
    for f in files:
        print("   ", f)
    shutil.rmtree(d)
    print("  已删除:", not os.path.exists(d))
else:
    print("测试目录不存在，无需删除")

print("=== 最终 conf ===")
final = json.loads(open(CONF, encoding="utf-8").read())
print("  ", [(i["domain"], i["used"], i["appFlag"]) for i in final])
