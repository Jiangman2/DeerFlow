"""Run-owned confirmation observation independent of a browser connection.

Runner holds its existing Sandbox execution lease. This monitor never creates
an Agent/session or new consent. Its lifetime is bounded by that original run;
shutdown cancels/joins the observer before the Host client is closed. Durable
SQL claims and Host cancellation guards still own effects, not this timer.
"""

from __future__ import annotations

import asyncio
import time

from deerflow.ppt_confirmation.lifecycle import ConsentConflict


def public_status(snapshot: dict | None) -> dict | None:
    """No paths, chosen payloads, credentials or unverified model narration."""
    if snapshot is None:
        return None
    gate = snapshot.get("gate")
    if snapshot["cancelled"]:
        return {"state": "cancelled", "message": "本次 PPT 确认流程已停止，不再自动确认。"}
    if gate is None:
        return None
    state = gate["state"]
    if state == "waiting_user":
        automatic = snapshot["policy"]["auto_enabled"] and gate["auto_block_reason"] is None and gate["due_at"] is not None
        message = "PPT 已到达官方确认阶段；本次任务已开启自动模式，完整等待 60 秒后按页面默认选项确认，无需打开预览。" if automatic else "PPT 已到达官方确认阶段，当前需要人工确认；任务尚未完成。"
        if gate["auto_block_reason"] not in {None, "manual_policy"}:
            message += " 自动确认已暂停，请检查当前内容并人工确认。"
    elif state in {"submission_pending", "submission_unknown"}:
        message = "PPT 确认请求正在核实；尚未取得有效回执，不会重复提交或越过确认阶段。"
    elif state == "confirmed":
        message = "已核实 PPT 官方确认回执，正在等待原任务继续；这不代表 PPT 已完成。"
    elif state in {"continuation_pending", "continuation_unknown"}:
        message = "PPT 已确认，正在核实原任务的继续状态；不会另起一个并行任务。"
    elif state == "continuing":
        message = "PPT 已确认，原任务已继续执行。"
    else:
        message = "PPT 确认内容或任务状态已改变，请查看当前确认状态。"
    return {"gate_id": gate["gate_id"], "stage": gate["stage"], "state": state, "due_at": gate["due_at"], "auto_block_reason": gate["auto_block_reason"], "message": message}


class ConfirmationMonitor:
    def __init__(self, controller, scope, *, interval=5.0, continuation=None):
        if not 0 < interval <= 5:
            raise ValueError("invalid_confirmation_poll_interval")
        self.controller, self.scope, self.interval = controller, scope, interval
        self.operation = None
        self._last = None
        self._pending = None
        self._degraded = False
        self.continuation = continuation
        self._task_receipt = None
        self._completed_wait_since = None
        self.snapshot = None

    def observe_task(self, payload):
        if not isinstance(payload, dict) or payload.get("task_id") != self.scope.task_id or payload.get("thread_id") != self.scope.thread_id:
            raise RuntimeError("confirmation_task_scope_mismatch")
        keys = ("task_id", "thread_id", "status", "artifacts", "process_cleanup_complete", "cancel_requested", "session_id", "confirmation_protocol", "last_continuation_gate")
        self._task_receipt = {key: payload.get(key) for key in keys}
        if payload.get("status") != "completed":
            self._completed_wait_since = None

    def hold_completed(self):
        """Keep original run/lease while its native confirmation is waiting.

        A no-artifact CLI exit is not by itself an official handoff. Give the
        independent observer a bounded chance to establish the actual gate;
        otherwise fail visibly instead of silently claiming task completion.
        """
        task = self._task_receipt or {}
        if task.get("status") != "completed" or task.get("artifacts"):
            return False
        if task.get("cancel_requested"):
            raise RuntimeError("confirmation_task_cancelled")
        if self._completed_wait_since is None:
            self._completed_wait_since = time.monotonic()
        gate = (self.snapshot or {}).get("gate")
        if self.snapshot and self.snapshot["cancelled"]:
            raise RuntimeError("confirmation_task_cancelled")
        if gate and gate["state"] in {"waiting_user", "submission_pending", "submission_unknown", "confirmed", "continuation_pending", "continuation_unknown"}:
            return True
        if gate and gate["state"] == "continuing" and task.get("last_continuation_gate") != gate["gate_id"]:
            return True  # The latest Host response predates the acknowledged resume.
        if time.monotonic() - self._completed_wait_since > 45:
            raise RuntimeError("PPT Agent 已结束本轮，但未取得可继续的官方确认边界；原会话和项目保留，请核实状态后再继续。")
        return True

    def _notice(self, value):
        # At most one pending projection: rapid retry errors cannot grow memory
        # or flood the durable task stream. Revision-only heartbeats are hidden.
        if value is not None and value != self._last:
            self._last = value
            self._pending = value

    def take_notice(self):
        value, self._pending = self._pending, None
        return value

    async def step(self):
        try:
            snapshot = await asyncio.wait_for(self.controller.tick(self.scope), timeout=45)
            if self.continuation is not None and self._task_receipt is not None:
                snapshot = await asyncio.wait_for(self.continuation.tick(self.scope, snapshot, self._task_receipt), timeout=20)
            self.snapshot = snapshot
            self._degraded = False
            self._notice(public_status(snapshot))
        except ConsentConflict:
            # CAS loss is not a new dispatch permission. A later tick reads the
            # committed winner; no effect is retried by the monitor.
            self._notice({"state": "unverified", "message": "PPT 确认状态暂时无法核实；自动确认不会据此放行，正在重新读取。"})
        except Exception:
            self._degraded = True
            self._notice({"state": "unavailable", "message": "PPT 确认服务暂时不可用；自动确认暂停，原 Agent 状态继续独立查询。"})

    async def _run(self):
        # A newly admitted task has no gate, so recover is a no-op. On a runtime
        # takeover, prior downtime must never silently satisfy the countdown.
        while True:
            try:
                await asyncio.wait_for(self.controller.recover(self.scope), timeout=5)
                break
            except Exception:
                self._notice({"state": "unavailable", "message": "正在恢复 PPT 确认状态；未核实前不会自动确认。"})
                await asyncio.sleep(self.interval)
        while True:
            started = time.monotonic()
            await self.step()
            await asyncio.sleep(max(0.05, self.interval - (time.monotonic() - started)))

    async def __aenter__(self):
        if self.operation is not None:
            raise RuntimeError("confirmation_monitor_already_started")
        self.operation = asyncio.create_task(self._run(), name="ppt-confirmation-" + self.scope.task_id)
        return self

    async def __aexit__(self, *_):
        if self.operation is not None:
            self.operation.cancel()
            try:
                await self.operation
            except asyncio.CancelledError:
                pass


def create_monitor(client, host_url, owner_id, thread_id, task_id):
    from deerflow.persistence import get_session_factory
    from deerflow.persistence.ppt_confirmation.sql import ConfirmationRepository, Scope
    from deerflow.ppt_confirmation.continuation import ConfirmationContinuation
    from deerflow.ppt_confirmation.controller import ConfirmationController
    from deerflow.ppt_confirmation.host_owner import HostOwner

    factory = get_session_factory()
    if factory is None:
        raise RuntimeError("confirmation_store_unavailable")
    scope = Scope(owner_id, thread_id, task_id)
    repository = ConfirmationRepository(factory)
    owner = HostOwner(client, host_url, scope)
    return ConfirmationMonitor(ConfirmationController(repository, owner), scope, continuation=ConfirmationContinuation(repository, owner))
