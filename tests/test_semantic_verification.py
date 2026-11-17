from pathlib import Path

from utils.semantic_verification import SemanticVerifier


def test_browser_search_requires_submitted_query_and_visible_results(tmp_path: Path):
    verifier = SemanticVerifier(tmp_path)
    expected = {"kind": "browser_search", "query": "python tutorial"}

    passed = verifier.verify(
        expected,
        {
            "url": "https://www.youtube.com/results?search_query=python+tutorial",
            "title": "YouTube",
            "visibleText": "Python Tutorial for Beginners\nLearn Python with a complete beginner course and practical exercises.",
        },
        action_executed=True,
        state_changed=True,
    )
    failed = verifier.verify(
        expected,
        {"url": "https://www.youtube.com/", "visibleText": "Search Python tutorial"},
        action_executed=True,
        state_changed=False,
    )

    assert passed.expected_state_verified is True
    assert passed.user_objective_completed is False
    assert failed.expected_state_verified is False


def test_browser_video_requires_watch_url_and_title_but_not_transcript(tmp_path: Path):
    verifier = SemanticVerifier(tmp_path)
    result = verifier.verify(
        {
            "kind": "browser_video",
            "url_contains": "youtube.com/watch?v=",
            "title_contains": "Python tutorial",
        },
        {
            "url": "https://www.youtube.com/watch?v=video123",
            "title": "Python tutorial for beginners - YouTube",
            "visibleText": "0:00 / 12:34",
        },
        action_executed=True,
        state_changed=True,
    )

    assert result.expected_state_verified is True


def test_browser_video_checks_visible_text_when_requested(tmp_path: Path):
    result = SemanticVerifier(tmp_path).verify(
        {
            "kind": "browser_video",
            "url_contains": "youtube.com/watch?v=",
            "title_contains": "Python",
            "text_contains": "Functions and loops",
        },
        {
            "url": "https://www.youtube.com/watch?v=video123",
            "title": "Python tutorial - YouTube",
            "visibleText": "0:00 / 12:34",
        },
        action_executed=True,
    )
    assert result.expected_state_verified is False


def test_file_content_and_test_process_require_observed_evidence(tmp_path: Path):
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("def answer():\n    return 42\n", encoding="utf-8")
    verifier = SemanticVerifier(tmp_path)

    file_check = verifier.verify(
        {"kind": "file_contains", "path": "src/main.py", "text": "return 42"},
        {},
        action_executed=True,
        state_changed=True,
    )
    test_check = verifier.verify(
        {"kind": "test_exit", "command": "pytest -q"},
        {"exit_code": 0, "output": "3 passed"},
        action_executed=True,
        state_changed=None,
        objective_scope=True,
    )

    assert file_check.expected_state_verified is True
    assert file_check.user_objective_completed is False
    assert test_check.expected_state_verified is True
    assert test_check.user_objective_completed is True


def test_file_verifier_rejects_paths_outside_workspace(tmp_path: Path):
    verifier = SemanticVerifier(tmp_path)
    result = verifier.verify(
        {"kind": "file_exists", "path": "../outside.txt"},
        {},
        action_executed=True,
    )

    assert result.expected_state_verified is False
    assert "outside" in result.error.lower()


def test_api_response_verifier_checks_status_and_expected_fields(tmp_path: Path):
    verifier = SemanticVerifier(tmp_path)
    result = verifier.verify(
        {"kind": "api_response", "status": 201, "fields": {"data.id": "abc-1"}},
        {"status": 201, "body": {"data": {"id": "abc-1"}}},
        action_executed=True,
        state_changed=True,
        objective_scope=True,
    )

    assert result.expected_state_verified is True
    assert result.user_objective_completed is True
