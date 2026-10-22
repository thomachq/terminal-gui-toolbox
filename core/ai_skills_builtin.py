# -*- coding: utf-8 -*-
"""8 个内置运维技能（SOP 正文）。

来源：宝塔终端 `DEFAULT_SKILLS`（app.asar 里的 8 份 SKILL.md 原文）。
正文按「探测 → 检查 → 诊断 → 修复 → 注意事项」组织，纯 Markdown，
不含任何可执行脚本 —— 与源一致，技能只是**指令文本**，命令一律由模型
按 SOP 生成、经会话通道在目标机上执行。

自动生成，请勿手工编辑（改技能请改 data/skills/ 下的用户副本）。
"""
from __future__ import annotations

BUILTIN_SKILLS: dict[str, str] = {
    "server-performance-diagnosis": r"""---
name: 服务器资源与性能检查
description: 分析服务器 CPU、内存、磁盘、负载、进程资源使用与健康状态，定位性能瓶颈
tags: 服务器,资源,CPU,内存,磁盘,性能,负载,进程,健康检查,瓶颈,iowait
---

# 服务器资源与性能检查

## 目标

全面评估服务器资源使用情况与健康状态，定位性能瓶颈（CPU / 内存 / 磁盘 IO / 网络），并给出量化分级结论与可操作建议。

## 1. 环境探测（Probe）

- 发行版：`cat /etc/os-release`
- CPU 核心数：`nproc`
- 先确认基础工具存在：`which top free df vmstat iostat sar`，缺什么补装或改用替代命令

## 2. 检查（Check）

- 进程资源占用：`top -bn1 | head -20` / `ps aux --sort=-%cpu | head -20`
- 内存：`free -m`
- 磁盘：`df -h` / `df -ih`（inode）
- 负载：`uptime` / `cat /proc/loadavg`
- 资源快照：`vmstat 1 5`
- 磁盘 IO：`iostat -x 1 3`（无则试 `sar -u 1 5`）

## 3. 诊断（Diagnose）

按「正常 / 警告 / 紧急」分级输出，给出具体数值：

- **CPU**：持续 >80% 关注；>90% 紧急
- **内存**：使用率 >85% 预警；Swap 频繁使用说明物理内存不足
- **磁盘**：使用率 >80% 预警；>90% 紧急（含 inode 用尽）
- **系统负载**：1 分钟负载 > CPU 核心数 ×2 需关注
- **磁盘 IO**：iowait >20% 说明磁盘是瓶颈
- **网络 IO**：检查带宽使用率与连接数

## 4. 修复（Fix）

- 定位 TOP 进程后判断合理性：异常进程先确认再处理
- 磁盘紧张：找大文件/日志膨胀（`du -sh /var/log/*`、`find / -size +1G`），清理临时文件
- 内存不足：分析缓存 vs 实际占用，排查泄漏进程，必要时扩容
- 瓶颈明确后给具体命令/参数调整方案
- 修改/删除类操作前**先向用户确认并获得授权**

## 5. 注意事项

- 谨慎删除 /tmp 下的文件：先检查是否有相关进程的 sock/lock 文件，以免误删导致服务异常
- 只报实测数值，不编造；结论必须数据支撑
""",
    "security-diagnosis": r"""---
name: 安全诊断与加固
description: 检查服务器安全风险、异常进程、入侵迹象（挖矿、反弹 shell、Webshell）、SSH 安全配置与文件权限
tags: 安全,入侵,异常进程,挖矿,webshell,后门,加固,SSH,登录,文件权限,恶意任务
---

# 安全诊断与加固

## 目标

评估服务器安全风险，检测异常进程、可疑文件与入侵迹象，检查 SSH 等关键配置合规性，输出分级（高/中/低）安全报告与加固方案。

## 1. 环境探测（Probe）

- 发行版：`cat /etc/os-release`
- 确定登录日志路径（探测）：`ls /var/log/{secure,auth.log,auth}*` 2>/dev/null
- Web 根目录（探测）：`ls -d /www /var/www /usr/share/nginx/html` 2>/dev/null

## 2. 检查（Check）

- 异常进程：`ps aux --sort=-%cpu | head -20` / `ps aux --sort=-%mem | head -20`
- 网络连接：`netstat -antp | grep ESTABLISHED` / `ss -tnp`
- SUID 文件：`find / -perm -4000 -type f 2>/dev/null`
- 登录记录：`last -20`
- 失败登录：`grep "Failed password" /var/log/secure* /var/log/auth.log* 2>/dev/null | tail -20`
- 可疑文件：最近修改的脚本（`find /tmp /var/tmp -type f -mtime -1`、Web 目录 `.php` 近期改动）
- 恶意定时任务：`crontab -l` + `ls /etc/cron.* /var/spool/cron/`

## 3. 诊断（Diagnose）

- **SSH 安全**：是否禁用 root 登录、是否用密钥认证、端口是否修改（默认 22 高风险）
- **异常进程**：挖矿进程（高 CPU 且名可疑）、反弹 shell、可疑网络连接（陌生外连 IP）
- **可疑文件**：Webshell（一句话木马特征）、后门文件、异常 SUID 文件
- **登录记录**：异常登录 IP、失败登录次数激增
- **文件权限**：关键文件权限过大（/etc/passwd、配置文件 777 / 666）
- **定时任务**：恶意 crontab 条目（下载/执行远程脚本等）

## 4. 修复（Fix）

- 高危风险立即提醒用户并给加固步骤（均需授权后执行）：
- 加固 SSH：禁用 root 登录（`PermitRootLogin no`）、改端口、启用密钥认证、装 fail2ban
- 清除威胁：结束恶意进程、删除 Webshell/后门/SUID 异常、清理恶意 crontab、封禁异常 IP
- 收紧权限：`chmod` 关键文件为合理权限（如 600/640/755）
- 修改任何配置前先备份并验证语法，改动后重启对应服务

## 5. 注意事项

- 只报告实际检测到的安全信息，不推测未验证的威胁
- 删除文件/进程/封 IP 等不可逆操作必须先在报告中列明并获授权
""",
    "service-cron-diagnosis": r"""---
name: 服务与计划任务诊断
description: 检查系统服务运行状态与启动失败原因（Nginx、MySQL、PHP-FPM、Redis 等）、定时任务（Cron）执行状态
tags: 服务,启动失败,systemd,init.d,端口冲突,nginx,php-fpm,oom,cron,计划任务,定时任务
---

# 服务与计划任务诊断

## 目标

验证核心服务运行状态，诊断服务启动失败原因（配置/端口/权限/磁盘/OOM），检查 Cron 计划任务执行状态与失败原因。

## 1. 环境探测（Probe）

- 服务管理器：`ps -p 1 -o comm=`（systemd → `systemctl`，否则 `/etc/init.d/`）
- 确认目标服务进程：`ps aux | grep -E 'nginx|mysql|php-fpm|redis' | grep -v grep`
- 服务状态命令二选一：`systemctl status <svc>` 或 `/etc/init.d/<svc> status`

## 2. 检查（Check）

- 服务状态：`systemctl status <svc>` / `systemctl is-active <svc>`
- 启动日志：`journalctl -u <svc> --since today`（systemd）或服务日志文件
- 配置语法：Nginx `nginx -t` / Apache `httpd -t`（先 `which nginx httpd` 探测）
- 端口占用：`lsof -i :<port>` / `ss -tlnp`
- OOM：`dmesg | grep -i oom`（需 root）/ `journalctl -k | grep -i oom`
- Cron：`systemctl status crond`（或 `crond` / `cron` 服务名探测）、`crontab -l`、任务执行日志

## 3. 诊断（Diagnose）

- **服务状态**：`active (running)` 正常；`failed` / `dead` 异常
- **端口冲突**：服务所需端口被其他进程占用
- **配置文件**：语法错误（`nginx -t` 报错）
- **权限问题**：运行用户对数据/日志目录无写权限
- **磁盘空间**：磁盘满导致服务无法启动（`df -h` 交叉验证）
- **内存不足**：OOM Killer 杀掉了服务进程
- **Cron 失败**：crontab 表达式错误、脚本无执行权限（`chmod +x`）、Cron 环境 PATH 不完整需绝对路径、任务超时无合理上限

## 4. 修复（Fix）

- 配置错误：修正后先 `nginx -t`/`httpd -t` 验证语法再重启
- 端口冲突：改服务监听端口或释放占用端口
- 磁盘满：清理日志/大文件后重启
- OOM：调整服务内存占用或加内存
- Cron：修正表达式、`chmod +x`、命令用绝对路径
- **任何服务重启或配置修改必须先获得用户授权**

## 5. 注意事项

- 状态优先：先确认服务当前状态，再基于日志定位原因
- 修改配置前先备份；只报实测的服务状态与日志
""",
    "network-firewall-diagnosis": r"""---
name: 网络与防火墙诊断
description: 分析防火墙规则（firewalld、ufw、iptables）、端口开放状态、IP 黑白名单，以及域名 DNS 解析记录与生效状态
tags: 防火墙,端口,iptables,firewalld,ufw,防火墙规则,IP,黑名单,DNS,解析,dig,域名,网络
---

# 网络与防火墙诊断

## 目标

排查防火墙规则/端口访问问题与 DNS 解析问题：确认规则是否放行、端口是否可达、IP 策略是否正确、域名解析记录是否生效。

## 1. 环境探测（Probe）

- 识别防火墙类型：`systemctl is-active firewalld` / `systemctl is-active ufw` / `which firewall-cmd ufw iptables`
- 端口监听：`ss -tlnp` / `netstat -tlnp`（备用）

## 2. 检查（Check）

- **firewalld**：`firewall-cmd --list-all` / `--list-ports` / `--list-rich-rules` / `cat /etc/firewalld/zones/public.xml`
- **ufw**：`ufw status verbose` / `ufw status numbered` / `ufw app list` / `cat /etc/ufw/user.rules`
- **iptables**：`iptables -L -n` / `iptables -S` / `ip6tables -L -n`
- **DNS**：`dig <domain>` / `dig <domain> A` / `dig <domain> CNAME` / `dig <domain> MX` / `nslookup <domain>` / 指定服务器 `dig @8.8.8.8 <domain>`

## 3. 诊断（Diagnose）

- **服务状态**：firewalld / ufw / iptables 是否正常运行
- **端口规则**：常用端口（22、80、443、3306 等）是否放行
- **默认策略**：iptables INPUT 默认应为 DROP/REJECT；ufw 默认应 deny incoming
- **IP 拦截**：异常 IP 被拦截或放行
- **规则冲突/冗余**：存在冲突或冗余规则
- **持久化**：规则已保存，重启不丢失
- **DNS 记录**：A 指向正确服务器 IP；CNAME 别名正确；MX 邮件配置正确；TTL >600 会致修改生效慢（建议 ≤600）；解析延迟 >200ms 关注；解析结果不一致（疑似污染）

## 4. 修复（Fix）

- 放行端口：firewalld `firewall-cmd --add-port=<port>/tcp --permanent && --reload`；ufw `ufw allow <port>`；iptables `iptables -A INPUT -p tcp --dport <port> -j ACCEPT`（记得保存持久化）
- 修正 DNS：指向正确的 A/CNAME/MX 记录，提示 TTL 生效时间
- **任何防火墙规则修改必须先获得用户授权**，并给出可回滚方案

## 5. 注意事项

- 先确认防火墙类型和状态，再取规则，避免误判
- 只报实际查询到的规则与解析结果，对比预期配置给出差异
""",
    "website-ssl-diagnosis": r"""---
name: 网站与 SSL 诊断
description: 分析网站配置、运行状态与访问异常（502/504、响应慢），SSL 证书状态/有效期/证书链，以及访问流量异常
tags: 网站,nginx,apache,配置,502,504,SSL,证书,https,证书链,TLS,HSTS,流量,访问日志,网站诊断
---

# 网站与 SSL 诊断

## 目标

诊断网站无法访问/响应慢的问题（配置、进程、端口、数据库连接），检查 SSL 证书状态/有效期/证书链/TLS 配置，识别访问流量异常。

## 1. 环境探测（Probe）

- Web 服务类型：`ps aux | grep -E 'nginx|httpd|apache' | grep -v grep` / `which nginx httpd`
- 站点配置目录（探测）：`ls /etc/nginx/conf.d /etc/nginx/sites-enabled /etc/httpd/conf.d` 2>/dev/null
- 证书文件位置（探测）：从站点配置里读 `ssl_certificate` / `SSLCertificateFile` 指令

## 2. 检查（Check）

- 服务状态：`systemctl status nginx`（或 httpd）/ 端口监听 `ss -tlnp | grep -E ':80|:443'`
- 配置语法：`nginx -t` / `httpd -t`
- HTTPS 连接：`curl -vI https://<domain>`（看证书/握手/响应码）
- 证书详情：`openssl s_client -connect <domain>:443` / `openssl x509 -in <cert> -noout -dates -subject`
- 访问日志（探测路径后）：`tail -100 <access.log>`、`grep -c " 502 " <access.log>`

## 3. 诊断（Diagnose）

- **502/504**：上游（PHP-FPM/后端服务）未运行、连接失败或超时；检查 `systemctl status php-fpm`（或 php 版本探测）与 `php-fpm -t` 配置语法
- **响应慢**：DB 连接慢、PHP-FPM 进程耗尽、高负载、大流量
- **SSL 有效期**：剩余 30 天内过期预警；已过期紧急
- **域名匹配**：证书域名与实际访问域名一致（SAN）
- **证书链**：中间证书完整（缺链 → 浏览器警告）
- **TLS 配置**：不应使用 SSLv3 / TLS 1.0 / TLS 1.1；避免 RC4、DES 等弱加密套件；建议启用 HSTS
- **流量异常**：短时间内大量请求来自少数 IP（疑似攻击/爬虫）、4xx/5xx 比例偏高

## 4. 修复（Fix）

- 502/504：启动/修复上游服务（php-fpm、后端），`systemctl restart php-fpm nginx`（需授权）
- 证书：续期/更换过期证书、补全中间证书链、修正证书域名
- TLS：升级配置禁用不安全协议/弱套件、启用 HSTS
- **任何证书更新或配置修改必须先获得用户授权**，改动后 `nginx -t` 验证再 reload

## 5. 注意事项

- 全面检查启用 HTTPS 的站点，不遗漏
- 只报实测的证书信息与日志，不编造；按紧急（过期）/警告（30 天内）/建议分级
""",
    "database-diagnosis": r"""---
name: 数据库诊断
description: 分析 MySQL/MariaDB 运行状态、性能指标、慢查询日志、连接数与配置合理性，给出优化建议
tags: 数据库,mysql,mariadb,慢查询,连接数,缓冲池,innodb,锁等待,性能,my.cnf
---

# 数据库诊断

## 目标

诊断 MySQL/MariaDB 的性能问题与故障（运行状态、连接数、缓冲池命中、慢查询、锁等待、配置合理性），给出分级优化建议。

## 1. 环境探测（Probe）

- 服务状态：`systemctl status mysqld`（或 `mariadb`/`mysql`，用 `ps aux | grep -i mysqld` 确认）
- **配置文件**：MySQL 配置通常位于 `/etc/my.cnf`、`/etc/mysql/my.cnf`，先用 `ls` 探测，读取后再分析；从配置里的 `datadir` 定位数据/日志目录
- 慢查询日志（从配置 `slow_query_log_file` 读取实际路径，不预设）

## 2. 检查（Check）

- 服务状态：`/etc/init.d/mysqld status` 或 `systemctl status mysqld`
- 连接数：`mysql -e "SHOW STATUS LIKE 'Threads_connected'"` / `SHOW VARIABLES LIKE 'max_connections'`
- 缓冲池：`SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool%'`
- 锁等待：`SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_waits'`
- 慢查询：读取慢查询日志（探测路径）
- 配置参数：读 `my.cnf` 里 `innodb_buffer_pool_size`、`max_connections`、`slow_query_log` 等

## 3. 诊断（Diagnose）

- **连接数**：当前连接数 / `max_connections` 比例 >80% 预警
- **内存使用**：`innodb_buffer_pool_size` 应约为物理内存的 50%–70%
- **慢查询**：超过 1 秒的查询重点关注，检查是否缺索引
- **锁等待**：`innodb_row_lock_waits` 高 → 并发写冲突/长事务
- **磁盘 IO**：关注数据读写量与磁盘使用率
- **无法启动/连接拒绝**：磁盘满、配置错误、权限、端口冲突

## 4. 修复（Fix）

- 慢查询：补索引、优化 SQL 改写（`EXPLAIN` 验证）
- 配置：调 `innodb_buffer_pool_size` / `max_connections`，改后 `mysql` 校验并重启（需授权）
- 锁等待：排查长事务、缩小事务范围、优化索引
- 故障：修复磁盘/权限/配置后重启服务
- **任何修改配置或重启服务的操作必须先获得用户授权**

## 5. 注意事项

- 信息收集优先：给结论前必须收集足够的诊断信息，只报实际读取的数据
- 按紧急/重要/建议分级输出优化建议
""",
    "log-analysis": r"""---
name: 日志分析
description: 分析系统与应用日志中的错误和异常，按严重程度分级，通过时间线定位故障原因
tags: 日志,错误,异常,错误日志,nginx错误日志,php错误日志,故障定位,时间线,ERROR,WARNING
---

# 日志分析

## 目标

分析系统和应用日志中的错误/警告，识别异常模式与频繁错误，通过日志时间线定位问题发生的具体时间与原因。

## 1. 环境探测（Probe）

- 先确认要分析的日志类型与范围（系统 / Web / 数据库 / 应用），向用户确认时间范围
- 探测日志路径（不预设）：`ls /var/log/`、Web 错误日志 `ls /etc/nginx/` 里 `error_log` 指令定位、数据库错误日志从 `my.cnf` 的 `log_error` 读取

## 2. 检查（Check）

- 系统日志：`tail -200 /var/log/syslog`（或 `messages`，探测路径）
- 认证/安全日志：`tail -100 /var/log/secure` / `auth.log`
- Web 错误日志：`tail -100 <nginx_error.log>`（探测路径）/ `grep -i error <access.log>`
- 数据库错误日志：`tail -100 <mysql .err 日志>`（探测路径）
- 错误统计：`grep -c "ERROR" <log>` / 错误类型去重 `grep -oE 'ERROR[^,;]*' <log> | sort | uniq -c | sort -rn | head -20`

## 3. 诊断（Diagnose）

- **错误级别**：区分 ERROR / WARNING / INFO / DEBUG，优先 ERROR
- **错误频率**：频繁出现的错误优先处理（去重计数）
- **时间关联**：关联多个日志的时间线，找出因果（先出现什么、后崩溃什么）
- **关键错误**：导致服务不可用的致命错误
- **安全事件**：认证失败、权限拒绝等安全相关日志

## 4. 修复（Fix）

- 致命错误：定位到具体服务与配置后给修复命令
- 频繁错误：根据错误类型给根因（配置、依赖、权限、磁盘）与修复步骤
- 修改/重启类操作先获授权；给出排查方向与下一步建议

## 5. 注意事项

- 操作前确认日志类型和时间范围
- 输出按紧急/重要/建议分级，提供错误前后的完整上下文
- 只报实际读取到的日志内容，不编造
""",
    "file-ftp-diagnosis": r"""---
name: 文件与 FTP 诊断
description: 分析服务器文件结构、权限设置、磁盘占用（大文件/日志膨胀/临时堆积），以及 FTP 服务状态、账户权限与连接问题
tags: 文件,权限,磁盘,大文件,文件结构,ftp,pure-ftpd,vsftpd,被动模式,chroot,连接
---

# 文件与 FTP 诊断

## 目标

分析文件结构/权限/磁盘占用，识别安全隐患与大文件堆积；诊断 FTP 服务状态、账户权限与连接问题（认证、被动模式、目录越权）。

## 1. 环境探测（Probe）

- 确认目标路径：向用户确认要分析的文件/目录，避免误操作
- FTP 服务类型（先识别再诊断）：`ps aux | grep -E 'pure-ftpd|vsftpd'` / `which pure-ftpd vsftpd`；**未安装则停止 FTP 诊断**
- 配置文件（探测）：`ls /etc/pure-ftpd/ /etc/vsftpd.conf` 2>/dev/null

## 2. 检查（Check）

- 目录结构：`ls -la <path>` / `find <path> -maxdepth 2 -type d`
- 权限：`stat -c '%A %U:%G %n' <path>` / `ls -la` 查看权限位
- 磁盘占用：`du -sh <path>/* | sort -rh | head -20` / `df -h` / `find / -size +1G 2>/dev/null`
- 大文件/临时堆积：`find /tmp /var/tmp -type f -mtime +7` 等
- FTP 服务：`/etc/init.d/pure-ftpd status`（或 `systemctl status pure-ftpd`）/ `netstat -tlnp | grep :21`
- FTP 配置：读 pure-ftpd.conf / vsftpd.conf 里的 `PassivePortRange`、`ChrootLocalUser`、虚拟用户配置

## 3. 诊断（Diagnose）

- **权限过大**：敏感文件/目录 777 / 666、Web 目录可写、关键文件暴露
- **磁盘占用**：大文件、日志膨胀、/tmp 临时文件堆积
- **FTP 服务**：21 端口未监听、pure-ftpd/vsftpd 进程未运行
- **被动模式**：`PassivePortRange` 配置错误或防火墙未放行对应端口段
- **目录越权**：未启用 `ChrootLocalUser`，账户可访问目录外文件
- **认证方式**：是否使用虚拟用户认证、账户权限与目录限制是否正确

## 4. 修复（Fix）

- 权限：`chmod`/`chown` 修正不合理权限（先备份再改）
- 磁盘：清理大文件/过期临时文件（删除前确认无进程占用）
- FTP：修正被动模式端口段、启用 chroot、配置虚拟用户、放行防火墙端口（21 + 被动端口段）
- **任何修改配置、删除文件或重启服务必须先获得用户授权**

## 5. 注意事项

- 路径确认前置，避免误删/误改
- 只读优先：优先只读工具分析，修改前征得同意
- 发现权限过大、敏感文件暴露等安全问题时主动提醒
""",
}
