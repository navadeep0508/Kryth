"""Task-specific verification using independently observed state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlparse

from tools.base import ExecutionStatus


@dataclass(frozen=True)
class VerificationReport:
    action_executed: bool
    state_changed: bool | None
    expected_state_verified: bool
    user_objective_completed: bool
    execution_status: ExecutionStatus
    evidence: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["execution_status"] = self.execution_status.value
        return data


class SemanticVerifier:
    """Verify expected outcomes without trusting a tool's success flag alone."""

    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(workspace).resolve()

    def verify(
        self,
        expected: dict[str, Any],
        observed: dict[str, Any],
        *,
        action_executed: bool,
        state_changed: bool | None = None,
        objective_scope: bool = False,
    ) -> VerificationReport:
        evidence: dict[str, Any] = {"kind": expected.get("kind")}
        error: str | None = None
        passed = False
        if not action_executed:
            error = "The action did not execute; expected state was not checked."
        else:
            try:
                passed, evidence = self._verify_expected(expected, observed)
            except (OSError, TypeError, ValueError, KeyError) as exc:
                evidence["kind"] = expected.get("kind")
                error = str(exc)

        if action_executed and not passed and error is None:
            error = "Observed state did not satisfy the expected outcome."
        status = ExecutionStatus.STATE_VERIFIED if passed else ExecutionStatus.VERIFICATION_FAILED
        return VerificationReport(
            action_executed=action_executed,
            state_changed=state_changed,
            expected_state_verified=passed,
            user_objective_completed=bool(passed and objective_scope),
            execution_status=status,
            evidence=evidence,
            error=error,
        )

    def _verify_expected(
        self, expected: dict[str, Any], observed: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        kind = str(expected.get("kind", "")).strip().lower()
        evidence: dict[str, Any] = {"kind": kind}

        if kind == "browser_search":
            query = self._normalize(str(expected.get("query", "")))
            url = str(observed.get("url", ""))
            visible = self._text(observed.get("visibleText", observed.get("visible_text", "")))
            query_values = [
                self._normalize(value)
                for values in parse_qs(urlparse(url).query).values()
                for value in values
            ]
            query_submitted = bool(query and query in query_values)
            results = observed.get("results")
            result_count = len(results) if isinstance(results, list) else int(observed.get("result_count", 0) or 0)
            visible_results = result_count > 0 or len(visible) >= 80
            evidence.update({
                "query": query,
                "query_submitted": query_submitted,
                "url": url,
                "visible_results": visible_results,
                "result_count": result_count,
                "visible_text_chars": len(visible),
                "visible_text_sha256": self._hash(visible),
            })
            return query_submitted and visible_results, evidence

        if kind in {"browser_navigation", "browser_video"}:
            url = str(observed.get("url", ""))
            title = self._text(observed.get("title", ""))
            visible = self._text(observed.get("visibleText", observed.get("visible_text", "")))
            url_part = str(expected.get("url_contains", expected.get("expected_url", "")))
            title_part = self._normalize(str(expected.get("title_contains", expected.get("expected_title", ""))))
            text_part = self._normalize(str(expected.get("text_contains", "")))
            url_ok = bool(url and (not url_part or url_part.casefold() in url.casefold()))
            title_ok = bool(title and (not title_part or title_part in self._normalize(title)))
            text_ok = bool(visible and (not text_part or text_part in self._normalize(visible)))
            if kind == "browser_video" and not url_part:
                url_ok = "youtube.com/watch" in url.casefold()
            evidence.update({
                "url": url, "title": title,
                "url_match": url_ok, "title_match": title_ok,
                "visible_text_match": text_ok,
                "visible_text_sha256": self._hash(visible),
            })
            # Video pages often expose their title and canonical watch URL
            # before (or without) transcript/body text. A caller can still
            # request visible content explicitly via ``text_contains``.
            requirements = [url_ok, title_ok]
            if kind != "browser_video" or text_part:
                requirements.append(text_ok)
            return all(requirements), evidence

        if kind in {"file_exists", "file_contains", "code_changes"}:
            target = self._workspace_path(str(expected.get("path", "")))
            exists = target.is_file()
            text = target.read_text(encoding="utf-8", errors="replace") if exists else ""
            required = str(expected.get("text", ""))
            contains = bool(required) and required in text
            evidence.update({
                "path": target.relative_to(self.workspace).as_posix(),
                "exists": exists,
                "size_bytes": target.stat().st_size if exists else 0,
                "sha256": self._hash(text),
                "required_text_match": contains if kind != "file_exists" else None,
            })
            passed = exists and (kind == "file_exists" or contains)
            return passed, evidence

        if kind == "test_exit":
            exit_code = observed.get("exit_code")
            output = self._text(observed.get("output", ""))
            required = str(expected.get("output_contains", ""))
            passed = isinstance(exit_code, int) and exit_code == 0 and (not required or required in output)
            evidence.update({
                "command": str(expected.get("command", ""))[:300],
                "exit_code": exit_code,
                "output_contains_match": (required in output) if required else None,
                "output_sha256": self._hash(output),
            })
            return passed, evidence

        if kind == "api_response":
            status = observed.get("status")
            expected_status = expected.get("status")
            body = observed.get("body", {})
            field_expectations = expected.get("fields", {})
            field_matches = {
                str(path): self._field(body, str(path).split(".")) == value
                for path, value in field_expectations.items()
            }
            status_match = expected_status is None or status == expected_status
            passed = status_match and all(field_matches.values())
            evidence.update({
                "status": status,
                "status_match": status_match,
                "field_matches": field_matches,
                "body_sha256": self._hash(json.dumps(body, sort_keys=True, default=str)),
            })
            return passed, evidence

        raise ValueError(f"Unsupported or missing expected outcome kind: {kind or '(empty)'}")

    def _workspace_path(self, relative: str) -> Path:
        if not relative:
            raise ValueError("A workspace-relative path is required for file verification")
        target = (self.workspace / relative).resolve()
        if not target.is_relative_to(self.workspace):
            raise ValueError("File verification path resolves outside the active workspace")
        return target

    @staticmethod
    def _field(value: Any, path: list[str]) -> Any:
        for segment in path:
            if not isinstance(value, dict) or segment not in value:
                return None
            value = value[segment]
        return value

    @staticmethod
    def _text(value: Any) -> str:
        return " ".join(str(value or "").split())

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
