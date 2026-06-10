from __future__ import annotations

import os


def _clear_config_env(monkeypatch) -> None:
    for name in (
        "KRYTH_CLI_MAIN_MODEL",
        "KRYTH_CLI_PLANNER_MODEL",
        "KRYTH_CLI_SUMMARIZER_MODEL",
        "KRYTH_CLI_BASE_URL",
        "KRYTH_MAIN_MODEL",
        "KRYTH_PLANNER_MODEL",
        "KRYTH_SUMMARIZER_MODEL",
        "KRYTH_BASE_URL",
        "AICODER_MAIN_MODEL",
        "AICODER_PLANNER_MODEL",
        "AICODER_SUMMARIZER_MODEL",
        "AICODER_BASE_URL",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_config_apply_to_env_sets_primary_and_legacy_aliases(monkeypatch):
    from kryth import config

    _clear_config_env(monkeypatch)
    config.apply_to_env({
        "model": "main-test",
        "planner_model": "planner-test",
        "summarizer_model": "summarizer-test",
        "base_url": "http://example.invalid/v1",
        "api_key": "test-key",
    })

    assert os.environ["KRYTH_MAIN_MODEL"] == "main-test"
    assert os.environ["KRYTH_CLI_MAIN_MODEL"] == "main-test"
    assert os.environ["KRYTH_PLANNER_MODEL"] == "planner-test"
    assert os.environ["KRYTH_CLI_PLANNER_MODEL"] == "planner-test"
    assert os.environ["KRYTH_SUMMARIZER_MODEL"] == "summarizer-test"
    assert os.environ["KRYTH_CLI_SUMMARIZER_MODEL"] == "summarizer-test"
    assert os.environ["KRYTH_BASE_URL"] == "http://example.invalid/v1"
    assert os.environ["KRYTH_CLI_BASE_URL"] == "http://example.invalid/v1"
    assert os.environ["OPENAI_API_KEY"] == "test-key"


def test_config_apply_to_env_honors_existing_legacy_env(monkeypatch):
    from kryth import config

    _clear_config_env(monkeypatch)
    monkeypatch.setenv("KRYTH_BASE_URL", "http://legacy.invalid/v1")

    config.apply_to_env({
        "model": "",
        "planner_model": "",
        "summarizer_model": "",
        "base_url": "http://stored.invalid/v1",
        "api_key": "",
    })

    assert os.environ["KRYTH_BASE_URL"] == "http://legacy.invalid/v1"
    assert os.environ["KRYTH_CLI_BASE_URL"] == "http://legacy.invalid/v1"


def test_persistence_honors_legacy_home_alias(tmp_path, monkeypatch):
    from agent.persistence import SessionStore

    monkeypatch.delenv("KRYTH_CLI_HOME", raising=False)
    monkeypatch.delenv("KRYTH_CLI_NO_PERSIST", raising=False)
    monkeypatch.delenv("KRYTH_NO_PERSIST", raising=False)
    monkeypatch.setenv("KRYTH_HOME", str(tmp_path))

    store = SessionStore()
    meta = store.start_new(cwd=tmp_path)
    assert meta is not None

    store.append_message({"role": "user", "content": "hello"})
    store.flush()
    store.close()

    session_file = tmp_path / "sessions" / meta.project_hash / f"{meta.session_id}.jsonl"
    assert session_file.is_file()
    assert '"role": "user"' in session_file.read_text(encoding="utf-8")


def test_persistence_honors_no_persist_alias(tmp_path, monkeypatch):
    from agent.persistence import SessionStore

    monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
    monkeypatch.setenv("KRYTH_NO_PERSIST", "1")

    assert SessionStore().start_new(cwd=tmp_path) is None


def test_tool_registry_matches_specs():
    from agent.tools import TOOLS, TOOL_SPECS

    spec_names = {spec["function"]["name"] for spec in TOOL_SPECS}
    assert set(TOOLS) == spec_names
    assert len(TOOLS) >= 25


def test_permission_profiles_are_isolated():
    from agent.permissions import check_permission
    from agent.session import Session, pop_session, push_session

    session = Session(profile="readonly")
    token = push_session(session)
    try:
        assert check_permission("read_file", {"path": "x.py"}) == "allow"
        assert check_permission("write_file", {"path": "x.py"}) == "deny"
        session.profile = "default"
        assert check_permission("write_file", {"path": "x.py"}) == "ask"
    finally:
        pop_session(token)


def test_hermes_tool_calls_recover_from_partial_stream_text():
    from agent.llm import _recover_hermes_tool_calls

    text = """
    I will inspect the project.
    <tool_call>
    <function=read_file>
    <parameter=path>C:\\Users\\navadeep\\Documents\\astra-ai\\package.json</parameter>
    </function>
    </tool_call>
    <tool_call>
    {"name": "read_file", "arguments": {"path": "src/main.jsx"}}
    </tool_call>
    """

    calls = _recover_hermes_tool_calls(text)

    assert [c["function"]["name"] for c in calls] == ["read_file", "read_file"]
    assert "astra-ai" in calls[0]["function"]["arguments"]
    assert "src/main.jsx" in calls[1]["function"]["arguments"]


def test_list_files_trims_recovered_windows_path_whitespace(tmp_path):
    from agent.tools._file_ops import list_files

    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    result = list_files(f"\n  {tmp_path}  \n")

    assert "package.json" in result
    assert "(empty directory)" not in result


def test_list_files_missing_directory_is_not_empty(tmp_path):
    from agent.tools._file_ops import list_files

    result = list_files(tmp_path / "does-not-exist")

    assert result.startswith("[ERROR NOT_FOUND]")
