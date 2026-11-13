# 环境、字体、部署与验收

## 基准环境

实测环境完整记录见config/reference-environment.json：Windows x64、Python3.9.12、Microsoft Edge157.0.4341.42、Playwright1.60.0。requirements-reference.txt锁定本机实际包版本。它是复现基线，不代表这些版本永远适合企业生产；升级Python/浏览器/字体或依赖后重新做截图验收。

安装示例（需要企业软件源可访问或内部wheel镜像）：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-reference.txt
python tools/check_environment.py
python tools/reproduce.py --strict
```

离线企业环境由管理员准备相应Python、pip wheels、Edge安装包，不把仅提供版本清单说成已打包全部二进制运行时。Microsoft Edge可通过企业已批准镜像安装；本包默认使用msedge通道。可用REPORT_BROWSER_EXECUTABLE指定获准的相同版本浏览器路径。Linux可配置浏览器及字体运行，但不是本次逐像素承诺环境，必须重新验收，不能将缺少字体自动回退后的截图视作合格。

## 字体

使用Windows Microsoft YaHei（msyh.ttc、msyhbd.ttc）和Arial（arial.ttf、arialbd.ttf）。config/font-manifest.json保存文件大小和SHA256；tools/check_environment.py比对本机字体。严格复现还需要相同字重映射、DPI和浏览器。TTC/TTF未随ZIP复制；请从企业具有使用权限的Windows镜像/字体来源配置，字体权限是否允许导出到Linux由企业确认。`FONT_DIRECTORY`可指定核对目录，但仅用于指纹检查，不会自动安装或让浏览器加载该目录的字体。浏览器须能实际解析这些系统字体。

不要用宋体、黑体、思源黑体、苹方代替微软雅黑后声称“完全一致”；这些可作为另行批准的迁移方案，但会改变行宽、换行和图片高度。不要把截图作为背景伪装成可生成模板。

## 企业Agent能力

必须具备：本包目录读写；Python进程；无界面Edge/Chromium；可读原文的搜索/网页工具；企业凭证注入；北京时间；状态持久化；单实例锁。模型无特定厂商依赖，agent可通过shell调用本包；不需要Codex应用或个人技能路径。搜索和新闻选编不是Python自动抓取器，本包提供规则/提示词/样例和校验，企业agent负责实际检索。

网络：API服务ip91.jkx.com.cn:6688；选用的权威原文站点，如news.cn、上市公司IR、gu.qq.com、finance.sina.com.cn、offshore-energy.biz等。白名单按实际公司公告扩展，不绕过登录、付费墙或验证码；换可靠来源并记录。供应商API字段/单位改变时拒绝混算并留档。

## 验收

1. `python tools/verify_manifest.py`校验交付文件，排除损坏/漏文件。
2. `python tools/check_environment.py`核对字体指纹、Python依赖、浏览器版本，将差异写acceptance/environment-check.json。
3. `python tools/reproduce.py --strict`离线渲染基准，HTML逐字节、PNG逐像素比较；结果在reproduced/comparison.json。same dimensions不等于逐像素一致，必须pixel_equal=true。
4. `python -m unittest discover -s app -p test_market_history.py`检查错误日期/重复代码/非法值、休市缺值、单位变更分段、历史数值与涨跌。
5. `python app/validate_inputs.py --date 2026-11-07`验证日期、数量、金融计算、新闻截稿、来源存在。
6. `python app/generate_report.py --date 2026-11-07 --png`完整装配后检查320/390/430不溢出，并打开PNG核对每个栏目。静态模板像素复现成功不等于未来每条新闻已真实核验。
7. 执行 `python -m unittest discover -s app -p test_workflow_gate.py`，使用模拟时间与网络验证暂停、开启及单次请求规则。配置默认enabled=true，不要把生产probe当无网络自测：它在有效窗口可能请求API。部署后再按执行规则验证真实触发，窗口外应返回OUTSIDE_WINDOW，当天完成应返回COMPLETE。

样例1302条API记录、五项股票与两项外汇、3条市场新闻与5条公司动态已随包提供。新闻来源网页可能变更，所以原文文本归档用于追溯；若后续有更正，须保留更正前后证据，不静默改写历史。

## 确实需要人工确定的事项

- 企业agent的工具调用格式、搜索供应商、工作目录和内部发布渠道由企业环境配置；没有访问权限时不能宣称已部署。
- 供应商期货历史合约名称和换月定义尚无书面文档，当前按接口原序列；未臆造连续合约或复权规则。
- API目标日休市不出虚假收盘，当前自动流程等待/留痕。若要改为跨休市自动发布，需要用户另行明确规则。
- 本包为定时已开启的企业配置，接收方须实际注册调度并开启服务；交付文件不代表企业内服务已经上线，也不恢复原电脑Codex任务。
