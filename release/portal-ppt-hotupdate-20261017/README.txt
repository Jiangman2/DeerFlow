2026-10-17 热更续接：门户、PPT 与前端镜像瘦身

最新续接索引（以下历史分段保留各自采集时刻）
当前状态以 CURRENT-PREPARATION-PROGRESS.json 和 CONTINUATION.json 为准。
最新r3已执行：owner5beb351dc97b4fa093ca3a0df477875e，通过准入并显示NETWORK_ROUND_1至4后返回controller_Refused，尚未进入NATIVE_*阶段。
完整安全报告2902B，SHA256 a354743903cf39c52f6f583d032638b6a80fb0e7a0a18ed10f80e2fde4f0b97f；待只读核对现存报告及四轮完成状态、双向结果和耗时，不能仅凭四轮进度认定隔离通过。
冻结network模块的Refused与controller本地类不同，safe_error将其具体校验原因缩为controller_Refused；具体触发条件仍待证据确认。实际清理及生产前后稳定性也待完整报告核验。
见native-runtime-attempt-r3/OPERATOR-SUMMARY.json和REVIEW-PENDING.json。保留全部once标记及旧证据，不重跑r3、不新发网络探针或调整时限，不修改CNI/PSA。
以下r3交付命令已经执行，作为历史记录保留：
当前交付 r3：native-runtime-qualification-r3/DELIVERY.json 和 ROOT-DELIVERY-REVIEW.json；六镜像已预载，无需重传或重新导入。
手动复制 Downloads/portal-ppt-native-qualification-20261017-r3/qualify-portal-ppt-native-20261017-r3.py 到 datax-01 的 /home/cnooc/。
仅在 datax-01 执行新版一次：sudo python3 -I -B -u /home/cnooc/qualify-portal-ppt-native-20261017-r3.py --qualify-native-once
脚本158872B，SHA256 c540fbe1cbaa6b5f15e5fad9635bf15c566ff94379b4290b4bbc5f9702f71a8c。
新隔离组件已通过本地真实Linux网络测试、8项定向检查和独立复核；控制器13项定向测试通过。仍须实际服务器准入、原生ARM64运行与最后结果核验，不能将本地测试计为热更完成。
r3只在自有测试Pod内关闭非环回接口、保留lo：首个普通init及固定proof subject仅授NET_ADMIN；业务容器保留原用户，全部能力集为零，启动前验证只读回执、真实Pod UID、netns及链路。
网络测试保持双方健康屏障、三轮双向拒绝和原60/240秒界限；结果明确为LINK_DOWN_LOOPBACK_ONLY，生产NetworkPolicy执行能力仍未证明。
不修改生产CNI或PodSecurity；若准入拒绝则停止。随后复用原Host/前端/PG和五步数据库兼容性探针，不连接生产数据库、不调用模型、不切换服务。
保留r1/r2的once标记、命名空间及全部证据；r3使用新标记。回传完整终端摘要和download_report对应REPORT.json，失败先不要重跑。
以下r2准备说明和所有旧命令均为历史记录，不再执行：
最新r2已执行，通过PRIORITY_ADMISSION_READINESS后在NETWORK_PROOF阶段返回network_convergence_deadline。
r2 owner为6ada1851c83e433aba56bfd91a13da83，报告2873B、SHA256 cc10c4d5996ed6d7fa16e9fa70399d0efaded3530e1833b8df1369102eaec5e2已精确核验。
实际撤回放行策略后三轮、六次跨Pod请求均SUCCESS，18次健康检查均成功；179条CLI命令无失败。已确定本次受控Pod隔离未生效，不能通过延长时间或RPC优化转为通过。
测试Pod与PriorityClass已按UID清理、deny-all保留、生产前后稳定。见native-runtime-attempt-r2/REVIEW.json及NETWORK-PROJECTION.json；正在评估只作用于自有测试资源的隔离方式，未授权安装或修改生产CNI。
已成功查询的网络组件只匹配到Flannel0.28.7，现存策略包括生产SkillHub PostgreSQL/Redis及监控/Loki；窄查询不用于证明全群组件绝对缺失。
当前准备方案见native-runtime-attempt-r2/ISOLATION-STRATEGY.json：在新自有测试Pod首个init中仅授NET_ADMIN，关闭其非环回接口、保留lo；后续业务容器能力集为零、证据卷只读并在启动前核对真实netns和链路。
新机制明确声明LINK_DOWN_LOOPBACK_ONLY，不能宣称生产NetworkPolicy已生效；保留真实三轮双向拒绝、双方健康屏障和既有时间限制。本地定向、真实内核测试与独审已完成，r3交付见最上方；尚未远端执行。
以下r2交付命令同样已经执行，作为历史说明保留。
最新原生验收已执行并在NETWORK_PROOF阶段失败，错误kubectl_nonzero_1；完整安全报告1771B、SHA256 fdd49101a4e8892b92945a30b7334f3f5b7d2436e13f590720f4a69f7f3b2a2c已精确核验。
首失败命令15为创建network-subject Pod，270B错误已精确核验：Pod直接指定Never但未指定PriorityClass，集群Priority准入按默认PreemptLowerPriority计算并拒绝冲突；命令16是失败后的只读回查。
本次owner为9a948f58627a40ecadd0a5ee1fa2fc7a，见native-runtime-attempt-r1/CAUSE-REVIEW.json。
当前交付r2：native-runtime-qualification-r2/DELIVERY.json及ROOT-DELIVERY-REVIEW.json；12项定向测试和独立复核通过。
手动复制 Downloads/portal-ppt-native-qualification-20261017-r2/qualify-portal-ppt-native-20261017-r2.py 到 datax-01 的 /home/cnooc/。
在datax-01执行新版一次：sudo python3 -I -B -u /home/cnooc/qualify-portal-ppt-native-20261017-r2.py --qualify-native-once
脚本143240B，SHA256 8a0f48570039680348d9a8ecb97b07eca875975a8a36f7778c8a5442d0c72273；复用原有预载镜像和全部探针。
r2创建唯一、禁止抢占且不作为全局默认的集群PriorityClass（值0），按UID绑定测试Pod并清理；在30秒总期限内读取默认ServiceAccount的UID并对首个精确Pod做服务器端dry-run，无持久化Pod。
之后在新专属命名空间执行既有原生隔离验收；旧r1报告、日志、once标记及命名空间保留。回传新的终端摘要和安全报告，失败不要重跑。
生产身份/形状前后稳定；两条临时允许策略均按UID清理，deny-all保留；尚未进入Host/Frontend/数据库验收。不重跑验收，不删除once标记或私人日志。
以下r1原生执行命令已经运行，作为历史交付说明保留，不要再次执行。
最新预载全部完成，完整报告24125B，SHA256 99135299a8615bcadc90af60ab6e3332665e9714826b847d9a1f5e48c426688a。
六镜像均与固定PROFILE逐项一致，content/unpack及CRI可见性PASS；最后三项真实退出码均0，Runner382/369层及Host129层Committed。
Host安全路径和PG16缓存manifest/config已绑定，见native-preload-complete-evidence-r1/REVIEW.json；无需再次预载任何镜像。
统一原生验收单文件执行包已交付，见native-runtime-qualification-r1/DELIVERY.json及ROOT-DELIVERY-REVIEW.json。
手动复制 Downloads/portal-ppt-native-qualification-20261017-r1/qualify-portal-ppt-native-20261017-r1.py 到 datax-01 的 /home/cnooc/。
在datax-01执行一次：sudo python3 -I -B -u /home/cnooc/qualify-portal-ppt-native-20261017-r1.py --qualify-native-once
默认调度器将所有临时测试Pod定向到datax-02；先验证双向网络隔离，再串行验收Host、Frontend、PostgreSQL安全配置和五步后端数据库兼容性。
执行会创建和清理专属命名空间内的测试资源及临时数据库，最终保留该命名空间和deny-all；不操作生产数据库、不调用模型、不切换生产服务。
本地21项控制器测试及最终2项补充测试通过，辅助阶段分别完成定向测试和独立复核；服务器原生执行结果仍待回传，ready_for_rollout=false。
只需复制这个小脚本，无需重传镜像包；失败或中断保留现场，不要重跑。

以下预载交付均为已执行历史，不再运行：
当前交付：native-remaining-three-r1/DELIVERY.json 与 ROOT-DELIVERY-REVIEW.json；8项新Linux定向测试及独立复核通过。
只需手动复制 Downloads/portal-ppt-remaining-three-20261017-r1/resume-portal-ppt-remaining-three-20261017-r1.py 到 datax-02 的 /home/cnooc/。
在datax-02执行一次：sudo python3 -I -B -u /home/cnooc/resume-portal-ppt-remaining-three-20261017-r1.py --resume-three-once
脚本42332B，SHA256 d608ae5de2fe75ec7ae9d57818fd418713a4cad888ce95ce9b4edd6ebac3e962；无需重传任何镜像包。
修正KEY/PARENT列误判；现场两条普通Active父层对应Runner第342/365层，旧条件误报可复现。实际目标解压冲突与原空间/链完整性门槛仍保留。
仅处理Runner正向、Runner兼容回滚和Host正向；前端、Gateway正向和兼容回滚只读复核。首失败停止，保存逐项真实退出码与私人日志。
先贴回本次完整终端输出并保留新download_report报告；失败或中断不要重跑。仍须原生运行验收才能生产热更。

以下为历史余四项结果及交付，不再执行旧命令：
最新余四项执行报告已核验：8881B，SHA256 77823e4308eac6d38f5576f463342591750833b3555681472d9804eae7e1a417。
Gateway兼容回滚30/30层完成、真实退出码0；随后Runner在快照门槛前停止，未启动Runner导入。前端/Gateway正向/兼容回滚三项已完成。
快照检查对整行token取交集，会将PARENT栏命中也视为冲突；正在结合只读Active/View现场核对，修正KEY/PARENT区别并准备余三项续接。
见native-remaining-preload-evidence-r1/REVIEW.json。下列余四项命令已执行，不要重跑；保留旧once标记与全部证据。

历史余四项交付：
当前交付：native-remaining-preload-r1/DELIVERY.json 与 ROOT-DELIVERY-REVIEW.json；10项新Linux定向测试及独立复核通过。
只需手动复制 Downloads/portal-ppt-remaining-preload-20261017-r1/resume-portal-ppt-remaining-20261017-r1.py 到 datax-02 的 /home/cnooc/。
在datax-02执行一次：sudo python3 -I -B -u /home/cnooc/resume-portal-ppt-remaining-20261017-r1.py --resume-remaining-once
脚本34358B，SHA256 438d6485e32d40c120f9ca81d69e21aec91a76d0a6a79a70873219b31c75a798；无需重传镜像包。
仅续接余下四项：Gateway兼容回滚、Runner正向、Runner兼容回滚、PPT Agent Host正向；Frontend和Gateway正向只读复核。
每项沿用原现场检查与空间预算，保留真实退出码及私人日志；任一失败停止，完成后还会最终核验六个镜像。
返回本次完整终端输出并保留新download_report对应REPORT.json；失败或中断不要重跑。尚未生产切换或原生运行验收。

已接受的Gateway结果（下方Gateway命令均为历史，不再执行）：
最新执行成功已核验：Gateway报告6504B，SHA256 c0732346dcb41b9a979984b1036bf67c05ce314c030551246f5c7ec3f20ab662。
Gateway61/61层Committed，content/unpack和CRI可见性PASS，真实导入退出码0、耗时112.815秒；Frontend只读复核PASS。
余下四镜像续接已交付，见最上方命令；前端和Gateway正向预载已完成，下列Gateway单项命令为已执行历史，不再运行。
见native-gateway-recovery-evidence-r1/REVIEW.json；尚未原生运行验收或生产切换。

历史Gateway单项交付：
当前交付：native-gateway-recovery-r1/DELIVERY.json 与 ROOT-DELIVERY-REVIEW.json；10项不同Linux定向测试及独立审查通过。
只需手动复制 Downloads/portal-ppt-gateway-recovery-20261017-r1/recover-portal-ppt-gateway-20261017-r1.py 到 datax-02 的 /home/cnooc/。
在datax-02执行一次：sudo python3 -I -B -u /home/cnooc/recover-portal-ppt-gateway-20261017-r1.py --recover-gateway-once
脚本25344B，SHA256 13b18107c4be2018d74e6b5d4fea00ab36b46ff091a4a601958da593dfbb5cec；无需重传大镜像。
仅处理Gateway，执行前复核原内容、引用、快照和空间条件，保存真实退出码及有界私人日志；Frontend只读核验，结束后停止。
先贴回此次完整终端输出，保留新download_report指向的REPORT.json；失败或中断不要重跑，保留全部旧回执、镜像和once标记。
本地交付完成，尚未在服务器执行；其余镜像预载、原生运行验收和生产热更仍待完成。

以下均为历史记录；其中“当前”“最新”和执行命令只描述各自当时状态，不替代上面的Gateway单项交付：
最新诊断报告已核验：13787B，SHA5198cea4e71a0d925e1d79a96d60eb44196e3785c3f298f575861a57cccbf411；见native-phase-b-diagnostic-evidence-r1。
Gateway61条chain采样均ABSENT；15秒失败日志窗口仅3条，未截断且无匹配错误。不能据此认定失败原因，旧CLI错误正文无法恢复。
正在准备仅Gateway一次受控尝试，保留真实退出码和有界私人日志；前端只读确认，余4镜像不自动执行。以下已运行命令仅保留历史，不重跑。
当前交付为只读失败诊断：native-phase-b-diagnostic-r1/DELIVERY.json，6项定向Linux检查及独审通过。
仅手动复制Downloads/portal-ppt-phase-b-diagnostic-20261017-r1/diagnose-portal-ppt-phase-b-20261017-r1.py到datax-02 /home/cnooc。
执行：sudo python3 -I -B -u /home/cnooc/diagnose-portal-ppt-phase-b-20261017-r1.py --collect
先返回此次完整终端摘要，保留它指向的新REPORT.json；诊断schema为portal-native-phase-b-failure-diagnostic.v1。它不导入/删除镜像或修改服务。
最新执行结果：Phase B续接已运行；日志确认前端导入及完整性检查返回后进入Gateway，Gateway解压返回import_nonzero，脚本停止。
以下Phase B交付命令已执行，不要再次运行；保留其once标记、部分导入和全部旧回执。
完整报告已从用户终端JSON重建并核验：12001B，SHA ff707b34f008c642c44127b2b363241426649bb34b42a894d5d83a66b1307f82，见native-phase-b-attempt-r1/REPORT.json和REVIEW.json。
Frontend content/unpack和CRI可见性均PASS；Gateway前预算仍PASS但仅余3,749,127B。原frozen run_fd未保留失败ctr的真实退出码/错误正文，不能以import_nonzero确定原因。
下一步为固定失败时段containerd日志和Gateway快照进度只读诊断；不重跑Phase B、不重复Frontend、不删除镜像或减少空间保留。
当前交付：native-phase-b-resume-r1/DELIVERY.json 与 ROOT-DELIVERY-REVIEW.json。
库存 REPORT(4).json 已核验：89,968B，SHA ccfb0e3efe75efab543da7a6596fd8137662a30f4a5abbf6f85660f7cac0105a。
24份阶段回执一致，Phase A已完成，Phase B尚未开始；无需重传六个镜像或重跑Phase A。
20:48:42采样根盘可用69,127,147,520B，比此前Phase B预算高118,912,263B；原因未确定，未执行归档或删除。
此采样不等于执行时空间检查通过。续接保留原预算、余量和运行中监测，并在每个镜像解压前重新检查内容、引用、缓存及空间。
只手动复制 Downloads/portal-ppt-phase-b-resume-20261017-r1/resume-portal-ppt-phase-b-20261017-r1.py 到 datax-02 的 /home/cnooc/。
在 datax-02 执行一次：sudo python3 -I -B -u /home/cnooc/resume-portal-ppt-phase-b-20261017-r1.py --resume-phase-b
脚本21,443B，SHA 6b20039722ebd7b17a03439deb717c8f587efc56667e72ba6eeb88816cb0513b；原10项Linux检查、2项绑定差异检查及独审通过。
完成后返回终端摘要与download_report所指REPORT.json。失败或中断保留现场，不重跑；旧r3和local-resume仅为历史命令。
预载完成后仍须原生运行验收；未切换生产服务，ready_for_rollout=false。
旧镜像备用分析见native-image-archive-inventory-r1/CANDIDATE-REVIEW.json；未命中保护集合不等于未使用，也不是删除许可。

以下为历史续接记录；其中“当前”“最新”仅指各段记录时刻，不替代上面的Phase B交付：
当前交付为只读节点库存工具 native-image-archive-inventory-r1/DELIVERY.json：21,785B，8项定向检查及独审通过。
datax-01 当前工作负载与保留修订的103个镜像引用已记录为保护集合；节点库存返回后再筛选归档候选。
只手动复制 Downloads/portal-ppt-image-inventory-20261017-r1/collect-portal-ppt-image-inventory-20261017-r1.py 到 datax-02 /home/cnooc/。
在 datax-02 执行：sudo python3 -I -B -u /home/cnooc/collect-portal-ppt-image-inventory-20261017-r1.py --collect
它不导入/导出/删除镜像，仅采集镜像、容器引用、空间与24份阶段回执；返回download_report及终端摘要。
仅Phase B的后继已做离线准备及10项检查，但仍处于不可执行状态，待回执绑定、独审与空间恢复；未交付。
最新目标本机续接完整报告已精确核验：10720B/SHA3b1c3d86...；六档Phase A内容导入PASS，Phase B解压尚未请求。
BEFORE_PHASE_B唯一失败项为根盘imagefs字节预算：可用68,801,347,584B、需要69,008,235,257B，缺206,887,673B；/opt和inode阈值通过。
不得重跑原r3、local-resume或Phase A；正在准备只读候选库存及仅Phase B续接，必要时按既有授权归档未使用且非回滚镜像，不降低空间保留政策。
现存报告：/var/lib/portal-ppt-native-r3-report-8e58cebd6e4e4fd9aec1d5a077bbcda6/REPORT.json。
保留全部状态回执和已传/封存镜像；后继须按已完成阶段续接。见 native-preload-attempt-r3-local-resume。
最新 r3 执行失败已审：六个档案已传到 datax-02，源端远程执行未取得有效结果，错误 remote_result_count。
源端完整报告6274B/SHA8ac6f5ad...已精确核验；此前目标 PREPARED_NO_IMPORT 回执2871B/SHA94ae14af...也已绑定。
source target_import_requested=true 是请求已发但结果未知，不能据此断定已导入或未导入。
不要重跑 r3 --distribute；保留全部档案与失败现场。目标本机薄续接已通过10项Linux定向检查及独立审查，复用既有上传，持原锁确认未进 RUN 后才继续。
当前交付：native-preload-r3-local-resume/DELIVERY.json 与 ROOT-DELIVERY-REVIEW.json。
只需复制 Downloads/portal-ppt-native-r3-local-resume-20261017/resume-portal-ppt-native-20261017-r3-local.py 到 datax-02 的 /home/cnooc/。
在 datax-02 执行一次：sudo python3 -I -B -u /home/cnooc/resume-portal-ppt-native-20261017-r3-local.py --resume-on-target
该续接不再SSH/SCP，无需重传六个大镜像；完成后返回download_report和终端摘要，失败不重跑。尚未原生运行验收或生产热更。
详见 native-preload-attempt-r3/REVIEW.json 与 RESUME-BINDING.json。下面 r3 原交付命令仅为历史，不再执行。
当前完整缓存报告已收到并核验：397 content、493 snapshot，原报告SHA5a3b7b99...匹配。
已完成93条缺失链/92唯一blob的本地分配扫描，44硬链接均指向同层先前普通文件。
r3两阶段预载单文件已完成离线资格与独立审查：先导入内容不解压，再用原始元数据小OCI触发解压，完整大包仅放目标/opt。
保留原20%或20GiB余量及额外2GiB；物理分配预算含4KiB取整、目录/对象/链接及64MiB元数据。
预算为实测证据支持的保守估算，非空间预留或严格文件系统上界；运行前及运行中复核空间和缓存。
用户已授权必要时归档旧镜像作为备选；尚未归档/删除镜像，必须先确认未在用且不承担回滚。
最新单文件以native-preload-r3/DELIVERY.json及ROOT-DELIVERY-REVIEW.json为准；仅手动上传Downloads/portal-ppt-native-preload-20261017-r3/preload-portal-ppt-native-20261017-r3.py。
在datax-01执行一次：sudo python3 -I -B -u /home/cnooc/preload-portal-ppt-native-20261017-r3.py --distribute
完成后返回终端摘要与download_report所指报告。镜像预载、原生验收、生产热更仍以服务器真实结果逐步确认。
旧r1/r2失败、原包和封存均保留；不重传本地大包，不重新运行离线安装或只读缓存采集。

以下为历史记录（较新的CURRENT索引及r3交付优先）：
最新缓存诊断已完成且前后稳定：Runner基底365层、Host基底128层可复用；尚未传包/导入。
根盘70,143,561,728B；现有2倍展开预算73,099,612,240B，差2,956,050,512B。
正在复核实际串行导入峰值，不能简单去掉一倍展开后直接放行（机械计算仅余87,033,776B）。
完整报告392598B SHA5a3b7b992a170204247d27a33b740b78d65f0d89c443032020669023c3e7ba15尚待手工带回。
现存报告：/var/lib/portal-ppt-native-cache-report-3654b51a0bd44c47b4d264fa204c0683/REPORT.json；不要重跑采集。
目标/opt是uid1000/gid1000/mode755，后继不能沿用datax01的root祖先检查或仅新建root子目录后按文本路径写入。
最新r2结果：源端六个镜像已校验并封存于/opt/portal-ppt-native-sealed-r2-e8104bf11adc4959ad0b6078e7c97990。
目标datax-02准备阶段被磁盘预算拦下，尚未传输或导入：根盘可用70,147,383,296B，旧完整副本预算需138,515,064,218B。
目标/opt可用1,762,182,729,728B；仅迁移目标封存副本仍不足，需核实精确镜像缓存后计算增量占用。
r2证据见native-preload-attempt-r2；不要重跑r2或离线安装器，后继复用现存源端封存，不再次复制9GB。
当前交付：native-cache-diagnostic-r1/DELIVERY.json，单个约90KB只读缓存诊断脚本。
手动复制Downloads/portal-ppt-native-cache-20261017-r1/collect-portal-ppt-native-cache-20261017-r1.py到datax-01 /home/cnooc。
执行sudo python3 -I -B -u /home/cnooc/collect-portal-ppt-native-cache-20261017-r1.py --collect。
它只seek既有源端tar外层header/小元数据，并对目标精确digest与snapshot链做两次只读投影，写私人报告；不传镜像、不导入。
13项解析/预算fixture与5项Linux源端小tar/权限fixture已通过；wire完整往返与SSH argv大小通过。
这是缓存占用诊断，不是生产发布许可；镜像实际导入、原生验收与最终热更仍待完成。
当前恢复：离线包及8个文件已在datax-01校验/解包成功；不要重新执行离线安装器。
r1预载停于源端/var/lib封存空间门槛，尚未复制大包、SSH、传输或导入镜像。
失败1140字节报告已从完整终端JSON精确重建并与SHA匹配，见native-preload-attempt-r1。
最新df/stat证明根盘可用54,902,648,832B低于74,599,941,530B门槛，inode充足；
/opt为独立XFS盘，uid0/gid1000/mode755，可用750,326,616,064B，原大副本预算需655,233,464,525B。
后继只将源端大封存副本放/opt新私人目录，控制证据仍/var/lib；六档身份和目标导入流程不变。
后继交付以native-preload-r2/DELIVERY.json及ROOT-DELIVERY-REVIEW.json为准，仅需再传一个小.py。
传输方式更正：用户本机不能直连服务器，只能手动拷贝；此前Windows SSH连接超时，未创建远端目录或传输。
因此改用Downloads/portal-ppt-native-offline-20261017-r1内的.tar和.sh两个文件手工传到/home/cnooc。
以cnooc执行bash /home/cnooc/run-portal-ppt-native-offline-20261017-r1.sh，自动验包、解到新目录并调用既有预载工具。
离线包及启动器以offline-delivery-r1/DELIVERY.json和ROOT-DELIVERY-REVIEW.json为准；下述直连上传记录仅为历史。
此前直连交付（已被离线方式替代）：Downloads/portal-ppt-native-preload-20261017-r1/Upload-To-Datax01.ps1。
上传本机五个既有候选归档（3818267648字节），Host候选复用datax-01现存归档。
上传完成后由操作员在datax-01执行固定preload_native.py --distribute，串行预载到datax-02。
交付工具及PROFILE/RUNBOOK已冻结并经两份独立审阅；Linux19项通过、Windows15项通过4项POSIX跳过。
上传脚本PS5/7十项用例通过，最终交付副本PlanOnly通过；尚未连接服务器或执行预载。
依据见native-preload-r1/ROOT-DELIVERY-BINDING.json与UPLOAD-DELIVERY.json。
这一步只准备镜像缓存，原生运行、生产备份/恢复及发布验收仍待完成，ready_for_rollout=false。
已接收并核验同事更新后的 R2 源码及 targeted-r1 完整报告；实际数据库头为
0019_digital_employee_agents，两张 PPT 确认表尚不存在。
Gateway 固定 manifest ef500864... 引用 config dff2b9d7...，对应基线70个实例。
导出工具已独立审阅并交付，记录见 DELIVERY-gateway-base-r1.json。
操作员已完成导出，31个唯一blob和29层通过校验；终端回执按原编码重建的
1952字节REPORT与操作员SHA完全一致，见 GATEWAY-EXPORT-RECONCILIATION.json。
Gateway 本地归档已收到，870312448字节及完整SHA与导出回执匹配；全部29层及
32项更新/1项回滚源码前置条件已通过。Runner全部365层及17项更新/4项回滚前提通过。
四个后端更新/兼容回滚 OCI 已完成构建，原镜像层及运行配置均保持一致。
汇总报告为 backend-bound-build-r1/remainder-r3/AGGREGATE-4-BUILD-RESULT.json，
SHA256为88b4cfc91b9df522a1aed8425d08837e59910b4dc9cd5122d5bacd3edd384443。
实际镜像数据库验证r4五场景全部PASS：Gateway升级、Runner重启、两者兼容回滚、Gateway再升级。
五步保持相同0020迁移头和旧/新合成数据快照，6容器与1网络均清理。
一次性PostgreSQL实测版本16.15；应用为本地ARM64模拟，尚不代表原生节点与完整服务启动通过。
此前本地Docker元数据兼容及延迟创建问题的失败证据保留；修正仅涉及验收工具，
四个候选镜像不需要重新构建。最终RUN SHA为
a0aa372437d3b399d3dae4deb82e4b9b1498a9037b6112f38035ca3642b55e23，
独立核对见ACTUAL-IMAGE-QUALIFICATION-RECONCILIATION.json。
前端 successor ARM64 镜像已完成构建、TypeScript、14组HTTP/投影检查及OCI验收。
最终归档124756992字节，SHA256为77135d822fb1a8c9cd0109ff1b2e58c5a23fbb32c22ce630993c670e2ebe4918。
源码身份包括原合并清单及 frontend-projected-fix-r1 的单文件修复；不得仅引用旧合并清单。
完整依据见 FRONTEND-FINAL-RECONCILIATION.json。运行范围为本机ARM64模拟，企业实测待完成。
Agent Host 12项增量已独立审阅；175个独立用例的通过回执已核对，原失败历史保留。
操作员已在datax-01完成Host实际基底128层扫描。完整报告已取回并独立审阅，
128层和13项源码前提均通过，原报告SHA为
27f3658cb5933bf1a7f5f4b3b0173ff761b2ab9c60fe6f3db42c936167da1048。
报告交付的home_owner_mismatch已通过复制现存报告解决，未重跑扫描或修改/home/cnooc权限。
独立审阅见host-base-evidence-r1/INDEPENDENT-REVIEW.json；允许绑定12文件离线追加。
浏览器可执行路径、Codex软链接和实际Playwright版本仍需原生运行验收。
绑定上述报告的Host离线构建脚本已交付到Downloads/portal-ppt-host-oci-append-20261017-r1，
SHA256为c874898b2ed9ebae307e7c7b975a64e3f20e539fb163bbdd7456c84b9ba533c8。
操作员已成功生成5337610240字节Host候选，129层、122个唯一blob；报告SHA为
dd054654163e7f9852f0a121419a93835b900adfb88085d90547ff0af07d1e5a。
候选manifest为sha256:540c96e05aa5c0c7709871494dd094047223eaaf6393b2117e9149c0d1467971。
完整报告及独立核对见host-candidate-evidence-r1；OCI留在datax-01，未导入或操作Kubernetes。
六个候选的统一身份与位置见merge目录CANDIDATE-IMAGE-INVENTORY.json；本地五包共3818267648字节。
下一步只读检查已交付Downloads/portal-ppt-native-preflight-20261017-r1，脚本SHA为
79460b994738d4abcd773f2e5223977c8b0dead109926bafcb447015de7e6b80。
完整70946字节报告已取回并核验，exitcode=0、identity_and_shape_stable=true。
报告SHA为96d971e837140ec1cd019d1f6e2b833b17a67c6f8066628c7510fbe50de5bafc。
四Deployment规格/镜像与targeted基线一致，启动/挂载/探针亦与原基线一致；无需重建候选。
datax-02为Ready的ARM64节点，PG固定digest已在该节点运行，可用独立emptyDir复用镜像。
13个Pod资源语义未解析，capacity_pass仍为false，known_requests=0不代表空闲。
下一步使用默认调度器、指定节点亲和、requests=limits和Never抢占策略作实际准入；
不直接指定nodeName绕过调度，不重复全量资源采集。Pending超时即失败并清理自有测试资源。
完整独立核对及调度选择见native-preflight-evidence-r1/REVIEW.json。
此检查尚不是原生运行通过；六候选预载、实际磁盘检查和专有网络隔离测试仍待完成。
native-preflight-r1/INDEPENDENT-REVIEW-DELIVERY.json保存具体命令与报告路径。
原生Host探针和五步DB复验设计已准备，尚未在服务器运行；不把离线fixture测试当原生验收。
PPT确认范围的既有明确授权见
PPT-CONFIRMATION-SCOPE-AUTHORITY.json，不要求重复确认，也不扩展到其他Skill。
这些准备未更新生产服务或数据库，尚未满足完整发布资格。

任务分工
原聊天 01a0d2d7-0015-7a73-93c3-791b713a47f6 继续 WeKnora 升级。
本目录用于此前五需求剩余内容、PPT 完善和前端瘦身的独立续接。
DeerFlow 聊天 GLM 适配与 WeKnora 的 GLM 富化是两个集成，不能混为一项。
不改变既有容量、镜像构建并发约束，也不把主工作区所有未提交改动打包。

已恢复的进度
1. 首页办公链接、更多、用户指南已单独上线；最后服务器记录的实际浏览器
   验收仍待补齐。后续跳转角标与图标文字居中方案已由用户选定。
2. 热门员工跨节点临时存储测试及 DeerFlow GLM 四项真实协议测试已有 PASS。
   这不替代管理员页面、正式数据、模型选择器或候选镜像验收。
3. 本地 v6.6.0 Quick 已生成三页 PPTX、三页备注与新鲜 PDF，逐页检查通过。
   产出后全卷证据复制超时已单独恢复，原非零回执保留，没有重复提交模型。
4. 发布专用 Runner 到实际 Gateway HTTP 路由及隔离 PostgreSQL 联调通过
   13 项测试、3 个子用例。范围是本地 amd64、合成凭据与数据。
5. 前端 standalone 瘦身已完成本地 amd64 构建与运行验证，11 层、约126MB OCI。
   native ARM64、生产启动命令/挂载、企业浏览器仍待验证。

CONTINUATION.json 保存上述历史依据路径与 SHA256。
RETAINED-SOURCE-CHECK.json 核对先前回执明确列出的四个关键源码文件；
它不是全部候选源码清单，也不是当前生产源码证明。

当前下一步
更新后的 R2 证据包已收到并验证，/opt/DeerFlow 的 1761 个范围内文件均与采集清单匹配。
Gateway 当前观察标签为 20261017-background-stream-953eb97e4aaf；本地已从这批
源码建立隔离候选，逐文件合入门户、聊天 GLM、浏览器、PPT 确认及前端瘦身增量。
合并结果与本轮验证见 POST-UPDATE-MERGE-QUALIFICATION.json 和
C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017/。
补证 REPORT.json 已收到，SHA256 为 1e81bc2146de5c90a535c4e518e2f3febdc2e59dfcfa7333cd2050930fc84132。
实际数据库头 0019、两张新表不存在、Runner 三个有效挂载文件和 Gateway 七个新增
路径均已核对；四组件前后身份稳定。详见 TARGETED-REPORT-REVIEW.json 与
TARGETED-RECONCILIATION.json，不需要重复完整基线采集。

本地 build-inputs-r1 已准备 Gateway32、Runner17、兼容回退 Gateway1/Runner4
精确文件增量，以及 Frontend667文件的干净上下文。四份后端输入均保持 UNBOUND；
13项离线补丁测试及独立重跑通过，未构建镜像。Runner 已有保留的精确 OCI 归档；
镜像内迁移只到0017，需同时带入已核对的有效 deps.py/0018/0019，不能只追加0020。
在一次性 PostgreSQL 中，候选0019→0020成功；旧 Gateway/Runner 源码启动均因
未知0020失败，添加仅0020迁移的兼容源码均启动成功且合成旧/新数据保留。
见 MIGRATION-ROLLBACK-QUALIFICATION.json；这是 amd64 源码级验证，回退镜像未构建。

当前剩余：Gateway精确基底归档/manifest绑定、Agent Host专用增量、Frontend ARM64
Node实际基础层与Gateway/企业CA构建输入、原生ARM64及企业端到端验收和迁移发布流程。
用户明确 datax-01 无 Docker，后续只读元数据工具使用已知 CRI/containerd endpoint。
本地 Docker 仅用于隔离资格验证，不是服务器操作要求。
旧报告和以下更新前对照保留为历史参考；尚未构建新生产镜像或发布。
协调状态见 PRODUCTION-UPDATE-HOLD.json 和 CONTINUATION.json。

新基线的 Gateway70、Frontend2、Runner1、Provisioner1 活动实例均Ready；另有一个
保留的 Provisioner Failed/Evicted API 对象。旧 Runner overlay 和 RUNNER-LOCK
不覆盖新增共享确认模块，不得直接续用；候选包含 0020_ppt_confirmation，实际0019
迁移头已核实，完整发布条件仍须补齐。本地验证不能替代生产 ARM64 与企业浏览器验收。

refresh_baseline.py 是只读采集器，不是发布脚本。它限定 deer-flow 命名空间，
读取指定 Deployment、其 selector 对应且 owner UID 匹配的实例及固定源码摘要。
所有 Gateway 实例都须核对；最多4路并发只是采样并发上限，不是副本数上限。
不读 Secret/ConfigMap，不调用模型/网站，不连接数据库，不写共享业务目录，
不构建、导入、迁移、重启或修改生产配置。唯一持久写入是采集主机上的新私有报告。
环境值、原始启动命令、探针命令及认证头不进入可分享报告。

数据库实际迁移头、Sandbox Agent Host 镜像/源码、Nginx 实际入口与企业端到端
验收仍需后续专用核对；本工具不启动 Sandbox 来采集这些条件。
完整采集仅代表观察完成；缺文件、版本差异、漂移和失败须保留，ready_for_rollout
始终为 false。不得把旧镜像不存在新功能模块误判成已经实现或已发布。

安全续接顺序
1. 只读采集并解析最新基线；不在变动中的快照上冻结构建。
2. 对照新生产来源合并员工、聊天GLM、浏览器、PPT和已定稿UI增量；运行受影响回归。
3. 按当前迁移头核对 0020 及完整依赖，准备备份/回退和原生 ARM64 候选。
4. 瘦身前端从干净 Node 基底构建；核对 node server.js、build-time Gateway URL、
   mounts、probes，禁止用旧 /app/frontend 整树挂载覆盖 standalone 应用。
5. 精确预载与隔离企业验收，覆盖真实登录、确认、取消、恢复、PPTX/PDF预览下载、
   管理员热门管理、模型选择器和内网浏览器。未验项不借用本地 PASS 核销。
6. 再准备受控发布。现有 datax-01 原生构建锁保持；本地 Docker 双通道测试
   不授权生产同机并行重构建/导入。

本目录新增准备材料未修改旧候选工作树、原服务器回执或生产状态。

2026-10-17 Provisioner 分类续接
操作员首次报告的 sha256 为
6960ea5ab2e3f23e2aac2c9b2f3085fd8019cf63a2a353875985cc639c91af5e。
首次只收到终端摘要，记录见 OPERATOR-BASELINE-REVIEW-20261017-r1.json。
Gateway70、Frontend2、Runner1 的采集窗口内状态与所采固定路径一致。
Provisioner 的 owned2/ready1 触发 r1 两项阻塞；后续只读输出显示：
st4p8 为 Failed/Evicted，ContainerStatusUnknown/137；vjpwx 为 Running/Ready。
见 PROVISIONER-LIFECYCLE-SUPPLEMENT-20261017.json。这能解释额外 API 终态对象，
不能证明节点上的旧进程已经清除，也不据此推定驱逐原因。原失败报告保留不改；
随后收到完整报告，关联结果见下方最新审查；后续状态不回填为原时刻状态。

独立 refresh_baseline_v2.py 修正 API 终态记录与活动实例的分类；
r1 脚本、执行入口、已交付 ZIP 与旧哈希继续保持原样。v2 不改变生产资源，
不把终态对象删除或运行时未知信息抹掉，也不放行 Pending/Unknown、仍运行的
删除中实例、活动实例缺镜像身份、矛盾状态或采集期间变化。
此项修正不要求操作员重采整个集群。完整报告用于逐文件摘要、启动挂载和身份核对；
实际迁移头与完整生产源码仍按前述顺序核对。

v2 本地验证完成：原16项测试加9项分类/反例测试，Windows25/25、离线Linux25/25
通过；独立复核通过，精确源码哈希及验证范围见 LOCAL-VERIFICATION-v2.json。
这份本地验证不更改操作员原始报告，也不代替原报告的 UID 关联和逐文件核对。

2026-10-17 完整报告已接收
本地原文件：C:/Users/15285/Downloads/202610171213/REPORT.json；SHA256与操作员
终端给出的6960ea5a...91af5e完全匹配。独立审查见 BASELINE-IDENTITY-REVIEW.json。
4组Deployment、75个owned Pod的已记录前后投影一致；74个Ready样本与身份相符，
源码指纹重算通过，98个采集步骤完成。两个补充UID均绑定原记录，vjpwx就是采样实例。
原两项错误来自st4p8无运行身份和r1全owned计数；原INCOMPLETE回执保留。
Failed/Evicted是同UID的后续观察，不据此证明原采集时刻phase或底层进程已消失。

CURRENT-BASELINE-LOCK.json固定报告中的4组镜像、固定路径和运行形态，供后续合并
核验使用，不表示完整源码树已经取得或可以发布。固定路径对照分别见
BACKEND-SOURCE-COMPARISON.json、FRONTEND-SOURCE-COMPARISON.json。
MIGRATION-LINEAGE-REVIEW.json以AST静态核对候选20个迁移：前19个与保留home树
字节一致，候选单头0020接0019；它没有连接生产数据库。主工作区的0036链不纳入候选。
这份报告之后用户确认Gateway更新仍在进行，以上锁和对照仅表示历史采集时刻。
最新实际数据库版本、完整生产构建源码/清单、镜像默认启动命令、Sandbox
Agent Host、原生ARM64及企业端到端验收仍待完成。不能以固定文件匹配推广为整树匹配。

2026-10-17 更新后采集 R2 已准备并交付
交付记录：DELIVERY-r2.json；本地验证：LOCAL-VERIFICATION-r2-DELIVERY.json。
包内先执行固定范围的v3运行基线；仅成功后采集 /opt/DeerFlow 的限定源码范围。
两个报告与源码归档汇入新的私有EVIDENCE.tar.gz，失败保留证据且不自动重试。
尚未在服务器执行，也未取得更新后的报告。收到证据后再核对源码差异；
旧报告、历史锁、r1/v2工具及原有交付均保持原样。
