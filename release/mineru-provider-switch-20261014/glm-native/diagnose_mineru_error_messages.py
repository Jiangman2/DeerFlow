#!/usr/bin/env python3
"""Read existing MinerU HTTP400/502 diagnostics, never call the provider.

Only fixed dictionary fragments leave transient memory. This is a lossy
projection of logged errors, not a raw response export or a recovery action.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import types
from concurrent.futures import ThreadPoolExecutor

HELPER = Path('/var/lib/weknora-failure-reasons-tool-4afakxcT/check.py')
HELPER_SHA = '0b26d89421bd1bde248450cc55164a1fb75dfdd9240ad6541573053244ebb9b1'
SINCE = '2026-10-17T07:45:00Z'
LIMIT = 2097152
TAIL = 15000
GROUP_LIMIT = 24
MESSAGES = ('msg', 'message', 'detail', 'error', 'error_message', 'error_description')
KEYS = MESSAGES + ('code', 'error_code', 'success', 'data', 'status', 'status_code',
                   'request_id', 'trace_id', 'timestamp', 'path', 'errors')
# Output fragments are compiled constants, NEVER match.group() text. Unknown
# words, numbers, filenames, task IDs, URLs and credential-shaped values vanish.
ENGLISH = '''a an the is are was were be been has have had not no none null true false
please try again later too many much more less maximum minimum max min limit limits
limited exceeded exceeds exceeding concurrent concurrency requests request rate quota
capacity available unavailable busy overloaded exhausted connection connections pool
queue full length size large small file files document documents format type mime pdf
image images upload uploaded multipart field fields required missing empty invalid
valid unsupported corrupted corrupt encrypted password protected parameter parameters
argument arguments validation failed failure error bad gateway upstream internal server
service backend model worker workers task tasks id identifier found exist exists
expired expire expiration timeout timed out deadline retry retries authentication
authorization unauthorized forbidden permission permissions denied token key api qps
tps rpm tpm balance insufficient daily total processing pending rejected reject
submission submit status result results method path url route resource resources
endpoint content encoding json body data form read reading parse parsing response
response_text disabled enabled allowed allow access restricted security policy
blocked sensitive safety detect detected detection audit application account user
tenant deleted stopped canceled cancelled success successful fail unknown exception
system network connect reset closed disconnect length payload runtime allocate memory
cpu gpu disk space storage malformed unexpected unable cannot can input output
bound boundary overflow underflow permitted permitted_requests task_id taskid'''.split()
CHINESE = '''请求过于频繁 请求太频繁 请求频繁 频率限制 超出限制 超过限制 资源不足 服务繁忙
系统繁忙 任务繁忙 超过最大 超出最大 并发限制 并发数量 并发数 最大并发 并发 请求数 连接数
连接池 已用完 已耗尽 耗尽 不足 限制 限流 配额 额度 余额 上限 队列 已满 满载 等待 重试
稍后 当前 可用 不可用 无可用 服务异常 服务不可用 系统异常 内部错误 内部服务器错误
服务器错误 网关错误 上游异常 访问频繁 访问次数 调用次数 调用频率 访问 被拒绝 拒绝
权限 无权限 未授权 授权 鉴权 认证 失败 无效 非法 有效 过期 令牌 密钥 用户 账户 租户
参数 参数错误 参数校验 校验 不合法 缺少 缺失 必填 必须 为空 不能为空 空值 格式 不支持
文件 文档 图片 类型 大小 过大 过小 长度 超过 超出 加密 密码 损坏 解析 上传 读取 输入 输出
任务 任务不存在 不存在 未找到 找不到 状态 结果 处理中 正在处理 未完成 已完成 已删除
失败原因 错误信息 错误 发生 连接 连接失败 超时 连接超时 读取超时 网络异常 断开 中断
地址 路径 接口 不允许 禁止 安全 内容 敏感 违规 审核 风控 检测 不通过 无法 不能
成功 关闭 停止 已停止 已取消 取消 启用 禁用 模型 资源 资源耗尽 显存 内存 磁盘 空间
最大 最小 数量 每秒 每分钟 每日 每天 每小时 处理 提交 查询 返回 异常 不正确 不匹配'''.split()
WORDS = set(ENGLISH + CHINESE)
WORD_RE = re.compile('|'.join(
    r'(?<![A-Za-z0-9_])' + re.escape(w) + r'(?![A-Za-z0-9_])'
    if w.isascii() else re.escape(w)
    for w in sorted(WORDS, key=lambda x: (-len(x), x))), re.I)
STRING_PREFIX = re.compile(r'"((?:[^"\\\x00-\x1f]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*)')


class DiagnosticFailure(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise DiagnosticFailure(code)


def emit(row):
    print(json.dumps(row, ensure_ascii=True, sort_keys=True, allow_nan=False), flush=True)


def safe_error(exc):
    return dict(check=str(exc) if isinstance(exc, DiagnosticFailure) else 'READ_UNAVAILABLE',
                error_type=type(exc).__name__, raw_error_printed=False)


def load_helpers():
    for p in (HELPER.parent,) + tuple(HELPER.parent.parents):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
             'unsafe_helper_parent')
    fd = os.open(str(HELPER), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        s = os.fstat(stream.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
             and not s.st_mode & 0o022 and s.st_size <= 1048576, 'unsafe_helper')
        raw = stream.read(1048577)
    need(hashlib.sha256(raw).hexdigest() == HELPER_SHA, 'helper_hash_changed')
    helper = types.ModuleType('frozen_error_reasons'); helper.__file__ = str(HELPER)
    exec(compile(raw, str(HELPER), 'exec'), helper.__dict__)
    diagnostic, observer, unused_database_reader = helper.load_helpers()
    return diagnostic, observer, helper


def project(text):
    # Suppress opaque values BEFORE vocabulary matching; otherwise a filename
    # such as "quota exceeded.pdf" could look like an upstream quota verdict.
    text = re.sub(r'(?i)\b(?:https?://|www\.)\S+|\bBearer\s+\S+', ' [REDACTED] ', text)
    text = re.sub(r'(?i)\b(?:api[_-]?key|token|password|authorization|secret)\s*[:=]\s*(?:"[^"]*"|\S+)',
                  ' [REDACTED] ', text)
    text = re.sub(r'(?i)[^\s<>"\']+\.(?:pdf|docx?|pptx?|xlsx?|png|jpe?g|zip|tar)\b', ' [REDACTED] ', text)
    text = re.sub(r'["\'][^"\']*["\']', ' [REDACTED] ', text)
    text = re.sub(r'<[^>]*>|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', ' [REDACTED] ', text)
    parts, pos = [], 0
    for match in WORD_RE.finditer(text):
        if len(parts) >= 64:
            break
        gap = text[pos:match.start()]
        if re.search(r'\w', gap) and (not parts or parts[-1] != '[REDACTED]'):
            parts.append('[REDACTED]')
        word = match.group().lower()
        need(word in WORDS, 'projection_dictionary_error')
        parts.append(word)
        pos = match.end()
    if re.search(r'\w', text[pos:]) and (not parts or parts[-1] != '[REDACTED]'):
        parts.append('[REDACTED]')
    return ' '.join(parts) if parts else '[NO_RECOGNIZED_TEXT]'


def decode_detail(detail, helper):
    original = detail
    detail = detail[:512]
    escaped = bool(re.search(r'\\u[0-9a-fA-F]{4}', detail))
    body, parsed, outer_decodes = None, False, 0
    for _ in range(3):
        try:
            body = json.loads(detail)
            parsed = True
        except (ValueError, RecursionError):
            parsed = False
            break
        if not isinstance(body, str):
            break
        detail = body[:512]; outer_decodes += 1
    fields, messages, codes = set(), [], []

    def walk(value, depth=0):
        if depth > 3 or len(messages) >= 6:
            return
        if isinstance(value, list):
            for child in value[:6]: walk(child, depth+1)
        elif isinstance(value, dict):
            for key in KEYS:
                if key not in value: continue
                fields.add(key); child = value[key]
                if key in ('code', 'error_code') and type(child) in (str, int):
                    if re.fullmatch(r'-?[0-9]{1,8}', str(child)): codes.append(str(child))
                elif key in MESSAGES and isinstance(child, str):
                    messages.append(child[:512])
                elif key in ('error', 'errors', 'data', 'detail') and isinstance(child, (dict, list)):
                    walk(child, depth+1)
    if parsed:
        walk(body)
    else:
        # The adapter truncates each body to 300 characters BEFORE logging.
        # Recover complete JSON string tokens, or a valid prefix ending before
        # the first incomplete escape. This is never proof of a full response.
        for match in re.finditer(r'"('+'|'.join(KEYS)+r')"\s*:\s*', detail):
            key = match[1]; fields.add(key)
            tail = detail[match.end():]
            if key in MESSAGES:
                token = STRING_PREFIX.match(tail)
                if token:
                    try: messages.append(json.loads('"'+token[1]+'"'))
                    except ValueError: pass
            if len(messages) >= 6: break
        code = helper.business_code(detail)
        if code is not None: codes.append(code)
    if not messages and not parsed and not detail.lstrip().startswith(('{', '[')):
        messages = [detail]
    if not messages and parsed and isinstance(body, str):
        messages = [body]
    messages = messages[:6]
    patterns = sorted(set(project(text) for text in messages))
    hints = sorted(set(helper.classify(text) for text in messages))
    return dict(body_format='JSON' if parsed else ('JSON_PREFIX' if detail.lstrip().startswith(('{','['))
                else ('HTML_OR_TEXT' if '<' in detail else 'TEXT')),
        unicode_escape_seen=escaped, outer_string_decodes=outer_decodes,
        known_field_names=sorted(fields), business_codes=sorted(set(codes)),
        message_patterns=patterns, reason_hints=hints,
        adapter_preview_may_be_truncated=len(original) >= 300,
        unknown_text_redacted=True, projection_is_lossy=True)


def aggregate(raw, diagnostic, helper):
    rows, stamps, total = {}, [], 0
    lines = raw.decode('utf-8', 'replace').splitlines()
    for line in lines:
        stamp = diagnostic.stamp(line)
        if stamp: stamps.append(stamp)
        match = re.search(r'Enterprise MinerU HTTP (400|502) during '+diagnostic.OPERATIONS+r':\s*(.*)$', line)
        if not match: continue
        total += 1
        descriptor = dict(http_status=int(match[1]), operation=match[2],
                          **decode_detail(match[3], helper))
        key = json.dumps(descriptor, sort_keys=True, ensure_ascii=True)
        if key not in rows:
            # Bounded aggregation even if upstream returns many distinct errors.
            if len(rows) >= 256: continue
            rows[key] = dict(descriptor, marker_lines=0, first_utc=stamp, last_utc=stamp)
        rows[key]['marker_lines'] += 1; rows[key]['last_utc'] = stamp
    ordered = sorted(rows.values(), key=lambda r: (-r['marker_lines'], json.dumps(r, sort_keys=True)))
    selected = ordered[:GROUP_LIMIT]
    return dict(coverage=dict(since_utc=SINCE, first_retained_utc=stamps[0] if stamps else None,
        last_retained_utc=stamps[-1] if stamps else None, bytes_read=len(raw), lines_read=len(lines),
        byte_limit_reached=len(raw)>=LIMIT, line_limit_reached=len(lines)>=TAIL, complete_history_proven=False),
        groups=selected, http400_502_marker_lines=total,
        omitted_marker_lines=total-sum(r['marker_lines'] for r in selected),
        patterns_are_not_full_responses=True, marker_counts_are_not_unique_documents=True)


def collect_one(obs, diagnostic, helper, pod):
    row = dict(pod=pod['metadata']['name'])
    try:
        raw = obs.call(['logs', pod['metadata']['name'], '-c', obs.container(pod)['name'],
            '--since-time='+SINCE, '--timestamps=true', '--tail='+str(TAIL),
            '--limit-bytes='+str(LIMIT)], timeout=25, limit=LIMIT+4096)
        row.update(aggregate(raw, diagnostic, helper))
    except Exception as exc:
        row['unavailable'] = safe_error(exc)
    return row


def collect(diagnostic, obs, helper):
    pods, before = diagnostic.inventory(obs)
    workers = [p for name in obs.WORKERS for p in pods[name]]
    need(len(workers)==16, 'worker_inventory_changed')
    emit(dict(message_diagnostic='RUNNING', phase='READ_EXISTING_16_WORKER_LOGS',
              provider_requests=0, database_queries=0, health_requests=0))
    unavailable, matched = 0, 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        for row in pool.map(lambda p:collect_one(obs,diagnostic,helper,p), workers):
            unavailable += int('unavailable' in row)
            matched += row.get('http400_502_marker_lines',0)
            emit(dict(mineru_error_messages=row))
    _, after = diagnostic.inventory(obs)
    stable = before==after
    emit(dict(message_diagnostic='COMPLETE' if stable and not unavailable else 'INCOMPLETE',
        baseline_stable=stable, unavailable_reads=unavailable, matching_marker_lines=matched,
        historical_errors_found=matched>0, raw_logs_saved=False, raw_messages_exported=False,
        database_queries=0, pod_exec=0, provider_requests=0, health_requests=0, production_writes=0,
        automatic_retry=False, documents_submitted=0, signals_sent=0,
        collection_is_not_recovery_or_expansion_approval=True))
    return 0 if stable and not unavailable else 1


def main():
    try:
        need(len(sys.argv)==1, 'no_arguments_supported')
        need(os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01', 'wrong_host_or_identity')
        emit(dict(message_diagnostic='RUNNING', phase='VERIFY_PINNED_READ_HELPERS'))
        return collect(*load_helpers())
    except Exception as exc:
        emit(dict(message_diagnostic='INCOMPLETE', failure=safe_error(exc), production_writes=0,
                  provider_requests=0, automatic_retry=False, raw_messages_exported=False))
        return 1


if __name__ == '__main__': sys.exit(main())
