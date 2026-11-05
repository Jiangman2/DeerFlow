# 0015 auth.log 轮转与完整归档

操作员只读检查确认 `/var/log/auth.log` 的根盘设备号为 64771、inode 为 1441870，约 5.08 GiB，由 rsyslogd 写入。权限是 root:root 0644，带有 `security.kysec` 属性。现有已检查规则未覆盖该文件，logrotate 3.15.1 模拟检查通过。原始回执见 `0015-auth-log-rotation-dryrun-field-20261028-r1.json`。这不是已清理回执。

新工具只处理这个已知日志，先核对原 inode、配置摘要、日志进程、两个文件系统和数据盘余量。根盘临时轮转目录和数据盘归档目录均以 0700 独占创建。通过两个空文件确认权限及扩展属性都能复制后，才允许实际轮转。

工具采用 logrotate 的同文件系统 rename/create，配置独立、不加载全局规则、不删除任何历史轮转，不使用 copytruncate。新文件恢复原权限与全部扩展属性后，仅向 rsyslog.service 主进程发送一次 HUP，并写入一条无业务内容的日志验证标记。日志进程 PID/启动标识须保持不变，新文件必须有该进程的写入句柄，旧文件必须没有其他打开句柄。应用服务不重启、不滚动，容器运行时不参与。

旧文件关闭后，按 16 MiB/s 复制至 `/mnt/data/portal-auth-log-archive-0015-20261028-r1/auth.log.20261028-r1`，回读按 32 MiB/s 验证 SHA256；同时保留并比较权限、所有者与扩展属性。再次确认旧文件没有打开句柄或内存映射、新日志仍由同一进程写入，且新旧文件身份稳定，写入并 fsync 校验记录后，才删除根盘上的旧副本。数据盘副本永久保留。任一步失败保留现场与副本，不自动重试或回滚。

约 5.08 GiB 的复制及校验通常约 8–12 分钟，系统繁忙时会延长，总时间限制 40 分钟。运行时以 nice 19 和 idle I/O 优先级执行，每约 10 秒显示字节进度。最终报告记录根盘实际可用增量，不能仅以文件逻辑大小推断释放量。

此工具只执行一次性轮转与转存，**未安装永久轮转策略**。成功后应继续核对新的 auth.log 句柄、空间和旧归档回执，并补齐持续轮转方案，防止再次增长。麒麟安全属性必须继续保留；不能把本工具的临时配置直接加入 cron，因为其中故意没有通用 postrotate 行为。

依据：

- [rsyslog 官方文件输出说明](https://docs.rsyslog.com/doc/reference/parameters/omfile-file.html)：外部轮转后使用 HUP 关闭并重新打开输出文件。
- [logrotate 官方手册](https://github.com/logrotate/logrotate/blob/main/logrotate.8.in)：rename/create 与 copytruncate 的语义；后者存在复制和截断之间丢失日志的窗口。

生产执行命令（只在 ecs-zdz-pro-0015；本文件归档不是重跑授权）：

```bash
sudo python3 -I -B -u /home/cnooc/rotate-relocate-portal-0015-auth-log-20261028-r1.py --rotate-relocate-auth-log-once
```

报告和 journal 保存在数据盘专用目录。失败时保留最后 JSON，不重复执行。不要直接清空 auth.log、运行全局 logrotate -f、清理容器镜像或重启日志/应用服务。
