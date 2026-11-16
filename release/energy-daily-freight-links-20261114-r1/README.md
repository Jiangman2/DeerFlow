# 油气日报 Skill 2.1.1 热更

本包只替换现有 `energy-market-daily` Skill，基线是已上线的 2.1.0。
本地准备完成不代表生产已经执行。

## 本次变化

- 航运运费缺少目标日报价时，按八项指标分别寻找原三个月窗口内最近的有效报价。HTML、PNG、JSON 和文字逐项保留实际报价日期、距目标日天数及目标日状态；涨跌使用实际报价日前的同口径有效值。未找到时仍显示缺失，不把旧价写成当日报价，不改变油气、金融或新闻规则。
- 尚未找到运费时，在原有采集时间预算内对历史缺值缓存补查一次；失败保留原证据并标记查找未完成。401/403 后不继续使用被拒绝的凭据请求更早日期。
- 从当前 `present_files` 真实回执提取对话 URL，与附件发布确认分别处理。成功或失败回执中已有 URL 都会保留，最终文字追加 `[点击查看完整日报](当前对话URL)`。不会根据示例域名、工作目录或历史任务猜测链接。

## 执行

把 `install-energy-freight-links-20261114-r1.py` 复制到 datax-01 `/home/cnooc/`，仅执行一次：

```bash
sudo python3 -I -B -u /home/cnooc/install-energy-freight-links-20261114-r1.py --install-freight-links-once
```

脚本已内嵌代码包。安装前备份当前 Skill，并复用已有 public 浏览器依赖；在实际 ARM64 镜像、UID/GID 1000、public 子挂载下生成完整样例和运费回退样例，检查真实输出后才切换。不会修改 Gateway、Provisioner、凭据、定时任务或历史报告；不调用供应商、不发送机器人消息。

请保留最后 JSON。若返回 `INCOMPLETE`，不要重跑或删除标记。若返回 `ENERGY_DELIVERY_SKILL_INSTALLED_VISIBILITY_PENDING`，表示切换已发生而普通沙箱可见性待核验，不应重复安装。生产真实取数、当前对话文件发布及 Work 端展示仍需新任务验收。

本次入口为 406,152 字节，SHA256：
`12525a5395a1744580c3705dcf24a27bca868f78f48ba6180388fa3339275697`。
完整文件校验值见 `SHA256SUMS.txt`，本地验证见 `LOCAL-VALIDATION.json`。
