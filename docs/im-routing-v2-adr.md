# ADR-01 ~ ADR-04：飞书分群会话路由

状态：已接受（2026-11，随 `0023_im_session_routing` 落地）
背景与完整设计见 [im-routing-v2-architecture.md](im-routing-v2-architecture.md)。

---

## ADR-01 历史引用恢复不移动当前指针

**背景**：用户在 `/new` 之后 @机器人 回复自己几天前的机器人回复，希望接着那条历史线聊。

**决策**：把这条消息路由到**被引用消息所属的 Thread 的最新上下文**，但
`im_session_pointers.thread_id` 与 `generation` 保持不变。因此恢复是一次性的：下一条普通
@消息仍进入当前指针指向的 Thread。

**实现**：`resolve_route()` 返回 `RESTORE` + `link.thread_id`；`ChannelManager._resolve_v2_route`
校验该 Thread 仍存在，然后 `_run_chat_on_thread(thread_id=...)` 直接在该 Thread 上跑，
不写指针；只记录 `im_message_links`（inbound）与 `im_event_inbox`（route_reason）。

**后果**：
- 用户既能“回到旧会话”，又不会因为一次引用把整个群的默认上下文切走（E09/E34）。
- 旧 Thread 的标题/元数据不会被改写成当前话题（沿用既有 `_update_thread_channel_metadata`
  行为，恢复路径 `created=False` 时也不写指针）。
- 同一个旧 Thread 被多人并发恢复时，靠 Thread 级串行执行保证顺序（E42）。

---

## ADR-02 群聊必须结构化 @ 本机器人才触发

**背景**：飞书群消息的文本里可以出现 `@DeerFlow`，也可能只是“点击回复”某条消息，二者都不
代表用户在叫当前机器人。

**决策**：只认平台结构化 mention 实体（`message.mentions[].id.open_id == bot_open_id`，
或富文本 `at` 节点），且发送者是本渠道已识别的机器人身份。`/new`、`/status`、回复旧消息
同样适用：没有 @ 就不建 Thread、不建任务、不跑 Run、不回复。

**实现**：
- `feishu.py::_message_mentions_bot` / `_content_mentions_bot` 只比较 open_id 实体；
  `bot_open_id` 未知时返回 False（失败关闭）并异步刷新。
- 门禁 `chat_type in FEISHU_GROUP_CHAT_TYPES`（含 `topic` 话题群），位于 `/connect` 处理之后、
  命令分类之前——因此 `/new` 也先过门禁。
- 纯文本 `@`、仅 reply、仅昵称一律不触发；`root_id` 只表示话题成员关系，不当引用。

**后果**：E22/E31/E32/E33 通过；代价是用户在群里忘 @ 时机器人沉默（符合需求）。

---

## ADR-03 Web 操作与飞书指针完全隔离

**背景**：用户可能在网页端查看旧 Thread、搜索历史，甚至在网页端新建 Thread 并发消息。

**决策**：`im_session_pointers` 只由飞书入站路由写入（`insert_pointer_if_absent` /
`cas_switch_pointer`）。Web 的线程创建/运行路径不触碰该表；V2 下适配器也不再写 JSON
`store.json` 指针。

**实现**：`_remember_thread_mapping()` 在 V2 下直接 return；Web 侧没有任何
`ImRoutingRepository` 依赖。

**后果**：E06/E39 通过。Web 目录（M3）将来只读 `im_message_links` + Gateway threads，
不会反向影响飞书路由。已知代价：Web 新建的 Thread 无法通过飞书引用恢复（没有 link），
这属于“未分类记录”，符合需求“不猜测归属”。

---

## ADR-04 不可验证的历史引用必须失败关闭

**背景**：路由改造中最危险的退化是“找不到原会话就悄悄用当前 Thread 回答”，用户会误以为
上下文已恢复。

**决策**：一旦引用被判定为“历史引用”，任何无法证明归属的情况都返回明确提示，并且
**不创建 Run、不使用当前 Thread、不修改指针**。

| 情况 | 错误码 | 行为 |
| - | - | - |
| 确认是本机器人消息，但 `im_message_links` 无记录 | `REPLY_MAPPING_MISSING` | 提示无法恢复 |
| 映射存在，但 Thread 已删除/无权访问 | `THREAD_DELETED` | 提示无法恢复 |
| 引用消息读不到、发送者类型未知 | `REPLY_SOURCE_UNKNOWN` | 提示无法确认来源 |
| 引用的是别的机器人/别的 app | `REPLY_SOURCE_UNKNOWN` | 拒绝，不回答 |
| 引用来自别的群 | `CROSS_CHAT_REFERENCE` | 拒绝，不回答 |
| 引用同群其他 owner 的本机器人回复 | `CROSS_OWNER_QUOTE`（**不是**失败） | 把公开文本注入发送者当前 Thread |

**实现**：`routing_v2.failure_message()` 统一中文文案；`ChannelManager._send_routing_failure()`
只发一条 outbound（`thread_id=""`），不调用 LangGraph；`_thread_exists()` 任何异常都视为
“不可验证”（fail closed）。

**后果**：E10/E12/E35/E36/E37/E40 通过。失败文案不泄露内部 id 或别人的会话是否存在。