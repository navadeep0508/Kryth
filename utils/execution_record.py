"""Workspace-scoped, bounded execution records for resumable agent turns."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.base import InterruptionType, ToolResult


_MAX_EVENTS = 500
_MAX_TOOL_CALLS = 200
_SECRET_KEY = re.compile(r"(?:api[_-]?key|token|secret|password|authorization|cookie|credential)", re.I)
_INLINE_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|secret|password|authorization|cookie|credential)\b\s*[:=]\s*)([^\s,;&]+)"
)
_URL_SECRET = re.compile(r"(?i)([?&](?:token|secret|password|auth|session|credential|api[_-]?key)=)[^&\s]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any, key: str = "") -> Any:
    if _SECRET_KEY.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _safe(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value[:100]]
    if isinstance(value, str):
        value = _INLINE_SECRET.sub(r"\1[redacted]", value)
        value = _URL_SECRET.sub(r"\1[redacted]", value)
        return value[:4_000] + ("...[truncated]" if len(value) > 4_000 else "")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:2_000]


class ExecutionRecorder:
    """Persist structured execution evidence inside the active workspace."""

    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / ".kryth" / "executions"
        self.path: Path | None = None
        self.record: dict[str, Any] | None = None

    def start(
        self,
        objective: str,
        *,
        task_id: str | None = None,
        plan: list[str] | None = None,
    ) -> str:
        task_id = task_id or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", task_id):
            raise ValueError("Task ID contains unsupported path characters")
        self.path = self.root / f"{task_id}.json"
        if self.path.is_file():
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = None
        else:
            existing = None
        if isinstance(existing, dict) and existing.get("status") in {"in_progress", "incomplete"}:
            self.record = existing
            self.record["status"] = "in_progress"
            self.record.setdefault("events", []).append({"at": _now(), "type": "resumed"})
        else:
            self.record = {
                "schema_version": 1,
                "task_id": task_id,
                "objective": _safe(objective),
                "status": "in_progress",
                "phase": "UNDERSTAND",
                "plan": _safe(plan or []),
                "tool_calls": [],
                "events": [{"at": _now(), "type": "started"}],
                "retry_history": [],
                "recovery_decisions": [],
                "verification_evidence": [],
                "context_budget_stats": {
                    "requests": 0, "rejected": 0, "estimated_input_tokens_total": 0,
                    "estimated_input_tokens_max": 0,
                },
                "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "started_at": _now(),
            }
        self._write()
        return task_id

    def find_incomplete(self, objective: str) -> str | None:
        """Find a same-objective unfinished record without crossing workspaces."""
        if not self.root.is_dir():
            return None
        normalized = " ".join(str(_safe(objective)).casefold().split())
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:20]:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                item.get("status") in {"in_progress", "incomplete"}
                and " ".join(str(item.get("objective", "")).casefold().split()) == normalized
                and path.stem == item.get("task_id")
            ):
                return path.stem
        return None

    def set_phase(self, phase: str) -> None:
        if self.record is None:
            return
        normalized = phase.strip().upper()
        allowed = {
            "UNDERSTAND", "PLAN", "INSPECT", "EXECUTE", "OBSERVE",
            "VERIFY", "RECOVER", "CONTINUE", "FINALIZE",
        }
        if normalized not in allowed:
            raise ValueError(f"Unsupported execution phase: {phase}")
        self.record["phase"] = normalized
        self._event("phase", phase=normalized)

    def set_plan(self, plan: list[str]) -> None:
        if self.record is None:
            return
        self.record["plan"] = _safe(plan)
        self._event("plan_updated", step_count=len(plan))

    def tool_started(self, call_id: str, name: str, arguments: dict[str, Any]) -> None:
        if self.record is None:
            return
        calls = self.record["tool_calls"]
        existing = next((call for call in calls if call.get("call_id") == call_id), None)
        if existing and existing.get("finished_at") is None:
            return
        calls.append({
            "call_id": call_id,
            "name": name,
            "arguments": _safe(arguments),
            "started_at": _now(),
            "finished_at": None,
        })
        del calls[:-_MAX_TOOL_CALLS]
        self._event("tool_started", call_id=call_id, name=name)

    def tool_completed(
        self,
        call_id: str,
        result: ToolResult,
        *,
        duration_ms: float,
        output_reference: str | None = None,
    ) -> None:
        if self.record is None:
            return
        calls = self.record["tool_calls"]
        call = next((item for item in reversed(calls) if item.get("call_id") == call_id), None)
        if call is None:
            call = {"call_id": call_id, "name": "unknown", "arguments": {}, "started_at": _now()}
            calls.append(call)
        call.update({
            "finished_at": _now(),
            "duration_ms": max(0, round(duration_ms, 2)),
            "success": result.success,
            "action_executed": result.action_executed,
            "state_changed": result.state_changed,
            "expected_state_verified": result.expected_state_verified,
            "user_objective_completed": result.user_objective_completed,
            "execution_status": result.execution_status.value if result.execution_status else None,
            "error": _safe(result.error),
            "verification": _safe(result.verification),
            "output_chars": len(result.output),
            "output_sha256": hashlib.sha256(result.output.encode("utf-8", errors="replace")).hexdigest(),
            "output_reference": _safe(output_reference),
            "recovery_action": _safe(result.metadata.get("recovery_action")),
        })
        if result.verification:
            self.record["verification_evidence"].append({
                "call_id": call_id, "at": _now(), "evidence": _safe(result.verification),
            })
        if not result.success:
            decision = {
                "call_id": call_id,
                "at": _now(),
                "retry_scheduled": False,
                "reason": "Inspect current state before retrying; no automatic replay was performed.",
                "suggested_recovery": result.metadata.get("recovery_action"),
            }
            self.record["recovery_decisions"].append(_safe(decision))
        self._event("tool_completed", call_id=call_id, success=result.success)

    def set_tool_output_reference(self, call_id: str, reference: str) -> None:
        if self.record is None:
            return
        call = next((item for item in reversed(self.record["tool_calls"]) if item.get("call_id") == call_id), None)
        if call is not None:
            call["output_reference"] = _safe(reference)
            self._write()

    def record_retry(self, call_id: str, reason: str, *, idempotent: bool) -> None:
        if self.record is None:
            return
        item = {"call_id": call_id, "at": _now(), "reason": reason, "idempotent": idempotent}
        self.record["retry_history"].append(_safe(item))
        self._event("retry_recorded", call_id=call_id, idempotent=idempotent)

    def record_context_budget(self, budget: dict[str, Any]) -> None:
        if self.record is None:
            return
        stats = self.record["context_budget_stats"]
        estimate = int(budget.get("estimated_input_tokens", 0) or 0)
        stats["requests"] += 1
        stats["rejected"] += int(budget.get("status") == "rejected")
        stats["estimated_input_tokens_total"] += estimate
        stats["estimated_input_tokens_max"] = max(stats["estimated_input_tokens_max"], estimate)
        stats["last"] = _safe(budget)
        self._event("context_budget", status=budget.get("status"), estimated_tokens=estimate)

    def record_usage(self, usage: dict[str, Any]) -> None:
        if self.record is None:
            return
        totals = self.record["token_usage"]
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            totals[field] += int(usage.get(field, 0) or 0)
        self._event("usage", total_tokens=int(usage.get("total_tokens", 0) or 0))

    def record_error(self, error: str, details: dict[str, Any] | None = None) -> None:
        if self.record is None:
            return
        safe_details = _safe(details or {})
        self.record.setdefault("recovery_decisions", []).append({
            "at": _now(),
            "retry_scheduled": False,
            "reason": "No automatic replay. Inspect the latest verified state and resume remaining work.",
            "error": _safe(error),
            "details": safe_details,
        })
        self._event("agent_error", error=_safe(error))

    def finalize(
        self,
        response: str | None,
        *,
        task_checkpoint: dict[str, Any] | None,
        interrupted: bool = False,
        interruption_type: InterruptionType | str | None = None,
        interruption_reason: str | None = None,
        interruption_exception: BaseException | None = None,
    ) -> None:
        if self.record is None:
            return
        checkpoint = task_checkpoint or {}
        verified = checkpoint.get("status") == "completed" and not checkpoint.get("pending_steps") and not checkpoint.get("failed_steps")
        if interrupted:
            status = "incomplete"
        elif verified:
            status = "completed_verified"
        elif response:
            status = "answered_unverified"
        else:
            status = "incomplete"
        self.record.update({
            "phase": "FINALIZE",
            "status": status,
            "user_objective_completed": bool(verified),
            "response_chars": len(response or ""),
            "task_checkpoint": _safe(checkpoint) if checkpoint else None,
            "recovery_information": (
                _safe(checkpoint) if status == "incomplete" and checkpoint else
                {"resume_from": self.record.get("phase")} if status == "incomplete" else None
            ),
            "finished_at": _now(),
        })
        if interrupted:
            self.record["interruption_type"] = str(interruption_type or InterruptionType.EXECUTION_INTERRUPTED)
            if interruption_reason:
                self.record["interruption_reason"] = _safe(interruption_reason)
            if interruption_exception is not None:
                self.record["interruption_exception_type"] = type(interruption_exception).__name__
                self.record["interruption_exception_message"] = _safe(str(interruption_exception))
            if checkpoint:
                self.record["last_verified_step"] = checkpoint.get("last_verified_state")
        started = self.record.get("started_at")
        try:
            elapsed = (datetime.fromisoformat(self.record["finished_at"]) - datetime.fromisoformat(started)).total_seconds()
            self.record["duration_seconds"] = max(0, round(elapsed, 3))
        except (TypeError, ValueError):
            self.record["duration_seconds"] = None
        self._event("finalized", status=status)

    def _event(self, event_type: str, **details: Any) -> None:
        if self.record is None:
            return
        events = self.record.setdefault("events", [])
        events.append({"at": _now(), "type": event_type, **_safe(details)})
        del events[:-_MAX_EVENTS]
        self._write()

    def _write(self) -> None:
        if self.path is None or self.record is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temp, self.path)
