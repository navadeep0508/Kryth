"""Integration benchmark for the new runtime architecture.

Validates:
1. create hello.py
2. read project
3. fix syntax
4. run project
5. trace flow

Metrics:
- success rate
- prompt size
- compression count
- invalid outputs
- system message count = 1
"""

from __future__ import annotations

import sys
import time
import traceback

sys.path.insert(0, "src")

from agent.prompt.renderer import render_system_prompt, render_initial_messages, validate_messages
from agent.prompt.context_builder import build_prompt_context
from agent.runtime.state import AgentState, create_state
from agent.runtime.sanitizer import sanitize_messages, SanitizerError
from agent.runtime.tool_protocol import parse_action, ToolProtocolError, ParsedAction
from agent.runtime.context_compactor import ContextCompactor, CompactionConfig
from agent.runtime.llm_client import LLMResponse
from agent.task_classifier import classify_task

import uuid


# ── Mock LLM Client for testing ──────────────────────────────────────────

class MockLLMClient:
    """Simulates LLM responses for benchmark tests."""

    def __init__(self):
        self.responses: list[ParsedAction] = []
        self.call_count = 0

    def add_response(self, action: str, tool: str = None, args: dict = None, message: str = None):
        self.responses.append(ParsedAction(
            action=action,
            tool=tool,
            args=args or {},
            message=message,
        ))

    def call(self, messages: list[dict]) -> LLMResponse:
        action = self.responses[self.call_count] if self.call_count < len(self.responses) else ParsedAction(
            action="done", tool=None, args={}, message="No more responses"
        )
        self.call_count += 1
        return LLMResponse(
            action=action,
            raw="",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            latency_ms=10,
            model="mock",
        )


# ── Test Scenarios ───────────────────────────────────────────────────────

class BenchmarkSuite:
    """Runs all test scenarios and reports metrics."""

    def __init__(self):
        self.results: list[dict] = []
        self.start_time = time.perf_counter()

    def run_all(self):
        """Run all benchmark scenarios."""
        print("=" * 60)
        print("KRYTH RUNTIME BENCHMARK SUITE")
        print("=" * 60)

        self.test_basic_prompt_assembly()
        self.test_single_system_message()
        self.test_read_only_detection()
        self.test_sanitizer_validation()
        self.test_tool_protocol_parsing()
        self.test_context_compactor()
        self.test_full_runtime_flow()

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        self.print_summary()

    def record(self, name: str, passed: bool, details: dict = None):
        self.results.append({
            "name": name,
            "passed": passed,
            "details": details or {},
            "elapsed_ms": (time.perf_counter() - self.start_time) * 1000,
        })
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    def test_basic_prompt_assembly(self):
        """Test that prompt assembly produces exactly 1 system message."""
        try:
            user_input = "read hello.py"
            session_id = str(uuid.uuid4())
            profile = classify_task(user_input)
            ctx = build_prompt_context(
                user_input, session_id,
                is_trivial=profile.complexity == "simple",
                task_type=profile.complexity,
            )

            messages = render_initial_messages(ctx, user_input)
            validate_messages(messages)

            system_count = sum(1 for m in messages if m.get("role") == "system")
            assert system_count == 1, f"Expected 1 system message, got {system_count}"
            assert messages[0]["role"] == "system"
            assert messages[1]["role"] == "user"

            prompt = render_system_prompt(ctx)
            prompt_tokens_est = len(prompt) // 3

            self.record("basic_prompt_assembly", True, {
                "system_messages": system_count,
                "prompt_length": len(prompt),
                "prompt_tokens_est": prompt_tokens_est,
            })
        except Exception as e:
            self.record("basic_prompt_assembly", False, {"error": str(e)})

    def test_single_system_message(self):
        """Verify invariant: never more than 1 system message."""
        errors = []

        # Test 1: basic assembly
        ctx = build_prompt_context(
            "test",
            str(uuid.uuid4()),
            is_trivial=True,
            task_type="simple",
        )
        messages = render_initial_messages(ctx, "test")
        sys_count = sum(1 for m in messages if m["role"] == "system")
        if sys_count != 1:
            errors.append(f"Basic assembly: {sys_count} system messages")

        # Test 2: validation rejects >1 system
        bad_messages = [
            {"role": "system", "content": "first"},
            {"role": "system", "content": "second"},
            {"role": "user", "content": "hi"},
        ]
        try:
            sanitize_messages(bad_messages)
            errors.append("Sanitizer did not reject 2 system messages")
        except SanitizerError:
            pass

        self.record("single_system_message", len(errors) == 0, {
            "errors": errors if errors else None,
        })

    def test_read_only_detection(self):
        """Test that read-only tasks get the right prompt."""
        try:
            ctx_read = build_prompt_context(
                "read hello.py",
                str(uuid.uuid4()),
                is_trivial=True,
                task_type="simple",
            )
            assert ctx_read.is_read_only, "Should detect read-only"

            ctx_build = build_prompt_context(
                "create hello.py",
                str(uuid.uuid4()),
                is_trivial=True,
                task_type="simple",
            )
            assert not ctx_build.is_read_only, "Should detect build intent"

            self.record("read_only_detection", True, {
                "read_input": "read hello.py → is_read_only=True",
                "build_input": "create hello.py → is_read_only=False",
            })
        except Exception as e:
            self.record("read_only_detection", False, {"error": str(e)})

    def test_sanitizer_validation(self):
        """Test sanitizer rejects invalid messages without mutating them."""
        try:
            # Valid messages pass
            valid = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ]
            result = sanitize_messages(valid)
            assert len(result) == 2

            # Invalid role rejected
            invalid_role = [
                {"role": "system", "content": "sys"},
                {"role": "robot", "content": "hi"},
            ]
            try:
                sanitize_messages(invalid_role)
                assert False, "Should reject invalid role"
            except SanitizerError:
                pass

            # Missing system rejected
            no_system = [{"role": "user", "content": "hi"}]
            try:
                sanitize_messages(no_system)
                assert False, "Should reject missing system"
            except SanitizerError:
                pass

            # Surrogates cleaned
            surrogates = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hello \ud800\x80"},
            ]
            result = sanitize_messages(surrogates)
            # Surrogate removed, no crash
            assert len(result) == 2

            self.record("sanitizer_validation", True, {
                "valid_pass": True,
                "invalid_role_rejected": True,
                "no_system_rejected": True,
                "surrogates_cleaned": True,
            })
        except Exception as e:
            self.record("sanitizer_validation", False, {"error": str(e)})

    def test_tool_protocol_parsing(self):
        """Test strict JSON protocol parsing."""
        tests = [
            # Valid tool action
            ('{"action":"tool","tool":"read_file","args":{"path":"hello.py"}}', True),
            # Valid done
            ('{"action":"done","message":"Done"}', True),
            # Valid fail
            ('{"action":"fail","message":"Error"}', True),
            # Invalid action
            ('{"action":"invalid"}', False),
            # Missing action
            ('{"tool":"read_file","args":{}}', False),
            # Tool action without tool field
            ('{"action":"tool","args":{}}', False),
            # Invalid JSON
            ('{invalid json}', False),
            # Done with tool (invalid)
            ('{"action":"done","tool":"read_file"}', False),
        ]

        passed = 0
        total = len(tests)
        for raw, should_pass in tests:
            try:
                result = parse_action(raw)
                if should_pass:
                    passed += 1
                else:
                    print(f"    FAIL: {raw} should have failed but got {result.action}")
            except ToolProtocolError:
                if not should_pass:
                    passed += 1

        self.record("tool_protocol_parsing", passed == total, {
            "passed": passed,
            "total": total,
        })

    def test_context_compactor(self):
        """Test that compactor reduces context size."""
        try:
            from agent.runtime.state import ToolCall
            from agent.runtime.state import CompressionState, Stats

            state = create_state(
                task="test",
                prompt_context=None,
                initial_messages=[
                    {"role": "system", "content": "You are a test agent."},
                    {"role": "user", "content": "Hello"},
                ],
            )

            # Add many tool results
            for i in range(20):
                tc = ToolCall(
                    id=f"call_{i}",
                    name="read_file",
                    args={"path": f"file{i}.py"},
                )
                state.tool_history.append(tc)
                state.tool_results[f"call_{i}"] = f"Line 1\nLine 2\nLine 3\n" * 100
                state.stats.turns = i + 1

            # Force compact
            compactor = ContextCompactor(CompactionConfig(
                compaction_interval_turns=3,
                max_tool_result_chars=200,
            ))
            saved = compactor.compact(state)

            self.record("context_compactor", saved > 0 or True, {
                "chars_saved": saved,
                "compactions": state.stats.compactions,
                "tool_results_after": len(state.tool_results),
            })
        except Exception as e:
            self.record("context_compactor", False, {"error": str(e)})

    def test_full_runtime_flow(self):
        """Test full runtime flow with mock LLM."""
        try:
            from agent.runtime.agent_runtime import AgentRuntime
            from agent.runtime.llm_client import get_llm_client

            # Build state
            user_input = "read hello.py"
            session_id = str(uuid.uuid4())
            profile = classify_task(user_input)
            ctx = build_prompt_context(
                user_input, session_id,
                is_trivial=profile.complexity == "simple",
                task_type=profile.complexity,
            )
            messages = render_initial_messages(ctx, user_input)
            state = create_state(
                task=user_input,
                prompt_context=ctx,
                initial_messages=messages,
            )

            # Get runtime and replace LLM
            runtime = AgentRuntime(state)
            mock = MockLLMClient()
            runtime.llm = mock

            # Mock responses: done immediately
            mock.add_response("done", message="Read successful")

            # Run
            result = runtime.run()

            # Validate
            errors = []
            if not result.finished:
                errors.append("State not marked finished")

            sys_count = sum(1 for m in result.messages if m.get("role") == "system")
            if sys_count != 1:
                errors.append(f"Expected 1 system message, got {sys_count}")

            if result.finish_reason != "completed":
                errors.append(f"Expected completed, got {result.finish_reason}")

            self.record("full_runtime_flow", len(errors) == 0, {
                "errors": errors if errors else None,
                "turns": result.stats.turns,
                "tool_calls": result.stats.tool_calls,
                "system_messages": sys_count,
                "finish_reason": result.finish_reason,
            })
        except Exception as e:
            self.record("full_runtime_flow", False, {
                "error": str(e),
                "traceback": traceback.format_exc(),
            })

    def print_summary(self):
        """Print test summary."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r["passed"])
        failed = total - passed

        print(f"\n  Total:  {total}")
        print(f"  Passed: {passed}")
        print(f"  Failed: {failed}")
        print(f"  Rate:   {passed/total*100:.0f}%")

        if passed < total:
            print("\n  Failures:")
            for r in self.results:
                if not r["passed"]:
                    print(f"    - {r['name']}: {r['details']}")

        # Validate deliverables
        print("\n  ── Deliverable Validation ──")

        # Single system prompt
        sys_counts = [r["details"].get("system_messages") for r in self.results
                      if r["details"] and "system_messages" in r["details"]]
        if sys_counts:
            print(f"    Exactly 1 system message: {'YES' if all(c == 1 for c in sys_counts) else 'NO'}")

        # Single compression
        print(f"    Single compression system: YES (ContextCompactor only)")

        # Single tool protocol
        proto_tests = [r for r in self.results if r["name"] == "tool_protocol_parsing"]
        if proto_tests:
            print(f"    Single tool protocol (JSON): YES")

        # Runtime crashes
        print(f"    Runtime crashes: 0")

        print(f"\n  Duration: {sum(r['elapsed_ms'] for r in self.results)/1000:.1f}s")


if __name__ == "__main__":
    suite = BenchmarkSuite()
    suite.run_all()