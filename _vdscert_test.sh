#!/bin/sh
# VDSM 上验证「只入库、不绑定」是否被 DSM 接受（C 档）
# 不改 system/、不改 SERVICES、不重启 nginx
A=/usr/syno/etc/certificate/_archive
TS=$(date +%Y%m%d-%H%M%S)
NEWID=tbts01

echo "=== 0. 备份 INFO ==="
cp -p "$A/INFO" "/root/INFO.bak.$TS" && echo "备份: /root/INFO.bak.$TS"
ls -l "/root/INFO.bak.$TS"

echo "=== 1. 生成测试证书（自签名，不涉及任何真实域名）==="
rm -rf /tmp/tbcert
mkdir -p /tmp/tbcert
cd /tmp/tbcert || exit 1
openssl req -x509 -newkey rsa:2048 -nodes -keyout privkey.pem -out cert.pem \
    -days 2 -subj "/CN=tb-selftest.local" -addext "subjectAltName=DNS:tb-selftest.local" 2>&1 | tail -2
cp cert.pem fullchain.pem
ls -l cert.pem fullchain.pem privkey.pem

echo "=== 2. 写入证书库 _archive/$NEWID ==="
mkdir -p "$A/$NEWID"
cp -p cert.pem fullchain.pem privkey.pem "$A/$NEWID/"
chown root:root "$A/$NEWID" "$A/$NEWID"/*
chmod 400 "$A/$NEWID"/*
ls -l "$A/$NEWID"

echo "=== 3. 更新 INFO（保留原格式：缩进3空格 + 冒号前空格）==="
python3 - "$A/INFO" "$NEWID" <<'PYEOF'
import json, re, sys
path, nid = sys.argv[1], sys.argv[2]
raw = open(path, encoding='utf-8').read()
before = json.loads(raw)
print("原有条目:", list(before.keys()))
before[nid] = {"desc": "toolbox-selftest", "services": []}
out = json.dumps(before, indent=3, ensure_ascii=False)
out = re.sub(r'^(\s*)"([^"]+)": ', r'\1"\2" : ', out, flags=re.M)
open(path, 'w', encoding='utf-8').write(out + "\n")
after = json.loads(open(path, encoding='utf-8').read())
print("写入后:", list(after.keys()))
print("新条目内容:", json.dumps(after[nid], ensure_ascii=False))
PYEOF

echo "=== 4. DSM 是否认这张证书（CRT.list）==="
/usr/syno/bin/synowebapi --exec api=SYNO.Core.Certificate.CRT method=list version=1 2>&1 \
    | grep -v 'Exec WebAPI' | grep -E '"id"|"is_default"|"is_broken"|"desc"|"common_name"' | head -20

echo "=== 5. 服务是否照常 ==="
ps -eo pid,args 2>/dev/null | grep -c '[n]ginx'
/usr/bin/nginx -t -c /etc/nginx/nginx.conf.run 2>&1 | tail -1
echo "16443 自测:"
curl -sk -m 5 -o /dev/null -w "  http_code=%{http_code}\n" https://127.0.0.1:16443/ 2>&1
