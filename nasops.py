# -*- coding: utf-8 -*-
"""NAS 侧操作：SSH/SFTP + sudo 提权。凭据只在内存中，不落盘。"""
from __future__ import annotations

import base64
import os
import posixpath
import shlex
import socket
import tempfile
import time
from pathlib import Path

import paramiko

CONF_PATH = "/var/packages/VPNCenter/etc/openvpn/openvpn.conf"
CONF_PERSIST = "/usr/syno/etc/packages/VPNCenter/openvpn/openvpn.conf"
KEYS_DIR = "/var/packages/VPNCenter/target/etc/openvpn/keys"
REAPPLY = "/usr/local/bin/vpncert-reapply.sh"

REAPPLY_SH = """#!/bin/sh
# 由 Client Cert Console 生成：DSM 重新生成 openvpn.conf 后，把客户端证书校验与 CRL 补回去。
CONF=/var/packages/VPNCenter/etc/openvpn/openvpn.conf
KEYS=/var/packages/VPNCenter/target/etc/openvpn/keys
[ -f "$CONF" ] || exit 0

if grep -q '^verify-client-cert none' "$CONF"; then
    sed -i '/^verify-client-cert none/d' "$CONF"
fi
if ! grep -q '^verify-client-cert' "$CONF"; then
    sed -i '1i verify-client-cert require' "$CONF"
fi
if ! grep -q '^crl-verify' "$CONF"; then
    echo "crl-verify $KEYS/crl.pem" >> "$CONF"
fi
logger -t vpncert "re-applied client-cert + crl-verify to openvpn.conf"
exit 0
"""


class NasError(Exception):
    pass


class NasSession:
    def __init__(self):
        self.client: paramiko.SSHClient | None = None
        self.sudo_pw: str | None = None
        self.host = ""
        self.port = 22
        self.user = ""
        self._sftp: paramiko.SFTPClient | None = None
        # 命令追踪钩子：tracer(display_cmd, rc, out, err, elapsed_ms)
        self.tracer = None

    # ---------- 连接 ----------
    def connect(
        self,
        host: str,
        port: int = 22,
        user: str = "admin",
        password: str | None = None,
        key_path: str | None = None,
        key_passphrase: str | None = None,
        timeout: int = 15,
    ) -> None:
        self.close()
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            cli.connect(
                hostname=host,
                port=int(port),
                username=user,
                password=password or None,
                key_filename=str(key_path) if key_path else None,
                passphrase=key_passphrase or None,
                timeout=timeout,
                # 指定私钥就只认这把：否则私钥解析失败时 paramiko 会静默转去试
                # ssh-agent 里的其他钥匙，出现「连上了但用的不是它」。
                allow_agent=(not key_path),
                look_for_keys=(not key_path and not password),
            )
        except paramiko.AuthenticationException:
            raise NasError("认证失败：检查用户名 / 密码 / 私钥（及私钥口令）")
        except (socket.timeout, TimeoutError):
            raise NasError(f"连接超时：{host}:{port} 不可达或未开 SSH")
        except Exception as e:
            raise NasError(f"连接失败：{e}")
        self.client = cli
        self.host, self.port, self.user = host, int(port), user

    def close(self) -> None:
        try:
            if self._sftp:
                self._sftp.close()
        except Exception:
            pass
        self._sftp = None
        try:
            if self.client:
                self.client.close()
        except Exception:
            pass
        self.client = None

    @property
    def connected(self) -> bool:
        if not self.client:
            return False
        tr = self.client.get_transport()
        return bool(tr and tr.is_active())

    def require(self) -> None:
        if not self.connected:
            raise NasError("尚未连接 NAS")

    # ---------- 执行 ----------
    def run(self, cmd: str, sudo: bool = False, timeout: int = 120) -> tuple[int, str, str]:
        self.require()
        if sudo:
            if not self.sudo_pw:
                raise NasError("该操作需要 sudo，请先在连接页填写 sudo 密码")
            full = f"sudo -S -p '' sh -c {shlex.quote(cmd)}"
        else:
            full = cmd
        t0 = time.time()
        stdin, stdout, stderr = self.client.exec_command(full, timeout=timeout)
        if sudo:
            stdin.write(self.sudo_pw + "\n")
            stdin.flush()
            try:
                stdin.channel.shutdown_write()
            except Exception:
                pass
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        if self.tracer:
            try:
                self.tracer(full, rc, out, err, int((time.time() - t0) * 1000))
            except Exception:
                pass
        return rc, out, err

    def sudo_ok(self, pw: str) -> bool:
        self.sudo_pw = pw
        try:
            rc, _, _ = self.run("id -u", sudo=True, timeout=20)
            return rc == 0
        except Exception:
            return False

    def sftp(self) -> paramiko.SFTPClient:
        self.require()
        if self._sftp is None:
            self._sftp = self.client.open_sftp()
        return self._sftp

    # ---------- 文件 ----------
    def read_text(self, path: str, sudo: bool = False) -> str:
        if sudo:
            rc, out, err = self.run(f"cat {shlex.quote(path)}", sudo=True)
            if rc != 0:
                raise NasError(f"读取 {path} 失败：{err.strip() or out.strip()}")
            return out
        with self.sftp().open(path, "r") as f:
            return f.read().decode("utf-8", "replace")

    def write_text(self, path: str, text: str, mode: str = "600") -> None:
        """先传 /tmp，再 sudo install 到位（保留 root 属主与权限）。"""
        tmp_local = None
        remote_tmp = f"/tmp/.vpncert_{os.urandom(4).hex()}"
        try:
            fd, tmp_local = tempfile.mkstemp()
            with os.fdopen(fd, "wb") as f:
                f.write(text.encode("utf-8"))
            self.sftp().put(tmp_local, remote_tmp)
            if self.tracer:
                try:
                    self.tracer(f"[SFTP] put {remote_tmp} ({len(text.encode('utf-8'))} 字节)",
                                0, "", "", 0)
                except Exception:
                    pass
            parent = posixpath.dirname(path) or "/"
            self.run(
                f"mkdir -p {shlex.quote(parent)} && install -m {mode} -o root -g root "
                f"{shlex.quote(remote_tmp)} {shlex.quote(path)} && rm -f {shlex.quote(remote_tmp)}",
                sudo=True,
            )
        finally:
            if tmp_local and os.path.exists(tmp_local):
                try:
                    os.unlink(tmp_local)
                except OSError:
                    pass

    # ---------- 探测 ----------
    def probe(self) -> dict:
        self.require()
        info: dict = {"host": self.host, "port": self.port, "user": self.user}
        rc, out, _ = self.run("cat /etc/VERSION 2>/dev/null | tr '\\n' ' '")
        info["version"] = out.strip()
        rc, out, _ = self.run("id -Gn")
        info["groups"] = out.strip()
        info["has_sudo_pw"] = bool(self.sudo_pw)

        conf = ""
        if self.sudo_pw:
            rc, out, err = self.run(f"cat {CONF_PATH}", sudo=True)
            conf = out
        else:
            rc, out, err = self.run(f"cat {CONF_PATH} 2>/dev/null")
            conf = out
        info["conf_ok"] = bool(conf.strip())
        info["conf"] = conf
        info["conf_path"] = CONF_PATH
        info["keys_dir"] = KEYS_DIR
        info["verify_mode"] = "none" if "verify-client-cert none" in conf else (
            "require" if conf.strip() else "unknown"
        )
        info["has_crl_line"] = "crl-verify" in conf

        params = {}
        for line in conf.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if parts[0] == "server" and len(parts) >= 2:
                params["vpn_net"] = parts[1]
            elif parts[0] == "port":
                params["port"] = parts[1]
            elif parts[0] == "proto":
                params["proto"] = parts[1]
            elif parts[0] == "auth" and len(parts) > 1:
                params["auth"] = parts[1]
            elif parts[0] == "comp-lzo":
                params["comp_lzo"] = True
            elif parts[0] == "mssfix" and len(parts) > 1:
                params["mssfix"] = parts[1]
        info["params"] = params

        rc, out, _ = self.run(f"ls -la {KEYS_DIR}")
        info["keys_listing"] = out

        ta = ""
        if self.sudo_pw:
            rc, out, _ = self.run(f"cat {KEYS_DIR}/ta.key", sudo=True)
            ta = out
        info["ta_key"] = ta
        info["ta_key_ok"] = "BEGIN OpenVPN Static key" in ta

        rc, out, _ = self.run("netstat -tln 2>/dev/null | grep -E ':(11094|1194)\\b' | head -3")
        info["listening"] = out.strip()
        return info

    # ---------- 部署 ----------
    def backup(self) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tar = f"/volume1/vpncert-backup-{stamp}.tar.gz"
        cmd = (
            f"tar -czf {tar} {shlex.quote(KEYS_DIR)} {shlex.quote(CONF_PATH)} "
            f"{shlex.quote(CONF_PERSIST)} 2>/dev/null; ls -l {tar}"
        )
        rc, out, err = self.run(cmd, sudo=True)
        if rc != 0 or not out.strip():
            # 回退到 /tmp
            tar = f"/tmp/vpncert-backup-{stamp}.tar.gz"
            cmd = (
                f"tar -czf {tar} {shlex.quote(KEYS_DIR)} {shlex.quote(CONF_PATH)} 2>/dev/null; ls -l {tar}"
            )
            rc, out, err = self.run(cmd, sudo=True)
            if rc != 0:
                raise NasError(f"备份失败：{err.strip()}")
        return tar

    def deploy_material(self, ca_pem: str, server_crt: str, server_key: str, crl_pem: str | None) -> list:
        steps = []
        self.write_text(f"{KEYS_DIR}/ca.crt", ca_pem, "644")
        steps.append(f"上传 ca.crt（{len(ca_pem)} 字节）")
        self.write_text(f"{KEYS_DIR}/server.crt", server_crt, "600")
        steps.append(f"上传 server.crt（{len(server_crt)} 字节）")
        self.write_text(f"{KEYS_DIR}/server.key", server_key, "600")
        steps.append("上传 server.key")
        if crl_pem:
            self.write_text(f"{KEYS_DIR}/crl.pem", crl_pem, "644")
            steps.append(f"上传 crl.pem（{len(crl_pem)} 字节）")
        return steps

    def patch_conf(self) -> list:
        rc, conf, err = self.run(f"cat {CONF_PATH}", sudo=True)
        if rc != 0 or not conf.strip():
            raise NasError(f"读取 openvpn.conf 失败：{err.strip()}")

        lines = [l for l in conf.splitlines() if l.strip() != "verify-client-cert none"]
        has_verify = any(l.strip().startswith("verify-client-cert") for l in lines)
        if not has_verify:
            lines.insert(0, "verify-client-cert require")
        if not any(l.strip().startswith("crl-verify") for l in lines):
            lines.append(f"crl-verify {KEYS_DIR}/crl.pem")
        new_conf = "\n".join(lines) + "\n"

        self.write_text(CONF_PATH, new_conf, "600")
        steps = ["移除 verify-client-cert none / 写入 verify-client-cert require"]
        if "crl-verify" not in conf:
            steps.append("追加 crl-verify 行")

        # 持久化副本（存在则同步）
        rc, _, _ = self.run(f"test -f {CONF_PERSIST}", sudo=True)
        if rc == 0:
            self.run(
                f"cp -f {CONF_PATH} {CONF_PERSIST}", sudo=True
            )
            steps.append("同步到持久化配置副本")

        self.write_text(REAPPLY, REAPPLY_SH, "755")
        steps.append(f"写入持久化修复脚本 {REAPPLY}")
        return steps

    def restart(self, wait: int = 8) -> str:
        rc, out, err = self.run("synopkg restart VPNCenter", sudo=True, timeout=180)
        log = (out + err).strip()
        time.sleep(wait)
        rc2, out2, _ = self.run("netstat -tln 2>/dev/null | grep ':11094' | head -2")
        return f"{log}\n端口状态: {out2.strip() or '未监听'}"
