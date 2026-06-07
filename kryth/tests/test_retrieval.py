"""Comprehensive tests for the KRYTH retrieval engine.

Covers:
  - File reading (normal, mmap, streaming, batch)
  - Encoding detection
  - Binary detection
  - fd file discovery
  - FTS5 index (build, search, incremental update)
  - AST structural search
  - Graphify adapter (fallback path)
  - Smart query router (engine classification)
  - Cache layer (xxhash, diskcache/memcache)
  - File watcher (availability checks)
  - Repo index with xxhash fingerprinting
  - New tool functions (fts_search, ast_search, graphify_query, search_smart)
  - Backward compatibility: read_file, grep, glob_files still work

Each test is self-contained and uses tmp_path fixtures so nothing
touches the real project tree.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)
    return path


def _write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# retrieval.config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults_are_booleans(self):
        from agent.retrieval import config
        assert isinstance(config.ENABLE_MMAP, bool)
        assert isinstance(config.ENABLE_FTS, bool)
        assert isinstance(config.ENABLE_CACHE, bool)

    def test_thresholds_are_positive(self):
        from agent.retrieval import config
        assert config.MMAP_THRESHOLD > 0
        assert config.STREAM_THRESHOLD > config.MMAP_THRESHOLD
        assert config.MAX_FILE_SIZE > config.STREAM_THRESHOLD

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("ENABLE_MMAP", "false")
        # Re-read module attribute via importlib to test override path
        # (config is module-level so we test the _env_bool function directly)
        from agent.retrieval.config import _env_bool
        assert _env_bool("ENABLE_MMAP", True) is False

    def test_env_int_override(self, monkeypatch):
        monkeypatch.setenv("MAX_CONCURRENT_READS", "16")
        from agent.retrieval.config import _env_int
        assert _env_int("MAX_CONCURRENT_READS", 8) == 16

    def test_env_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CACHE_TTL", "not_a_number")
        from agent.retrieval.config import _env_int
        assert _env_int("CACHE_TTL", 3600) == 3600


# ---------------------------------------------------------------------------
# retrieval.cache
# ---------------------------------------------------------------------------


class TestCache:
    def test_fast_hash_returns_hex_string(self):
        from agent.retrieval.cache import fast_hash
        result = fast_hash(b"hello world")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fast_hash_is_deterministic(self):
        from agent.retrieval.cache import fast_hash
        assert fast_hash(b"data") == fast_hash(b"data")
        assert fast_hash(b"data") != fast_hash(b"other")

    def test_fast_hash_accepts_str(self):
        from agent.retrieval.cache import fast_hash
        assert fast_hash("hello") == fast_hash(b"hello")

    def test_file_fingerprint(self, tmp_path):
        from agent.retrieval.cache import file_fingerprint
        f = _write(tmp_path / "test.py", "x = 1")
        fp1 = file_fingerprint(str(f))
        assert fp1 != ""
        # Same file → same fingerprint
        fp2 = file_fingerprint(str(f))
        assert fp1 == fp2
        # Modified file → different fingerprint
        time.sleep(0.01)
        f.write_text("x = 2")
        fp3 = file_fingerprint(str(f))
        assert fp3 != fp1

    def test_file_fingerprint_missing_file(self):
        from agent.retrieval.cache import file_fingerprint
        assert file_fingerprint("/nonexistent/path.py") == ""

    def test_mem_cache_set_get(self):
        from agent.retrieval.cache import _MemCache
        c = _MemCache()
        c.set("k", "v", expire=60)
        assert c.get("k") == "v"
        assert c.get("missing") is None

    def test_mem_cache_expiry(self):
        from agent.retrieval.cache import _MemCache
        c = _MemCache()
        c.set("k", "v", expire=0)  # expire immediately
        # Force expiry by manipulating the store
        c._store["k"] = (c._store["k"][0], time.time() - 1)
        assert c.get("k") is None

    def test_mem_cache_delete(self):
        from agent.retrieval.cache import _MemCache
        c = _MemCache()
        c.set("k", "v")
        c.delete("k")
        assert c.get("k") is None

    def test_dumps_loads_roundtrip(self):
        from agent.retrieval.cache import dumps, loads
        data = {"key": [1, 2, 3], "nested": {"a": True}}
        assert loads(dumps(data)) == data

    def test_get_cache_returns_something(self, tmp_path):
        from agent.retrieval.cache import get_cache
        cache = get_cache(str(tmp_path))
        assert cache is not None
        cache.set("x", 42, expire=60)
        assert cache.get("x") == 42

    def test_capabilities_keys(self):
        from agent.retrieval.cache import capabilities
        caps = capabilities()
        assert "xxhash" in caps
        assert "diskcache" in caps
        assert "orjson" in caps


# ---------------------------------------------------------------------------
# retrieval.file_reader
# ---------------------------------------------------------------------------


class TestFileReader:
    def test_read_text_small(self, tmp_path):
        from agent.retrieval.file_reader import read_text
        f = _write(tmp_path / "test.py", "hello world\nline 2")
        assert "hello world" in read_text(str(f))

    def test_read_bytes(self, tmp_path):
        from agent.retrieval.file_reader import read_bytes
        f = _write_bytes(tmp_path / "data.bin", b"\x00\x01\x02")
        assert read_bytes(str(f)) == b"\x00\x01\x02"

    def test_read_smart_small_file(self, tmp_path):
        from agent.retrieval.file_reader import read_smart
        f = _write(tmp_path / "test.txt", "content")
        assert read_smart(str(f)) == "content"

    def test_read_lines_basic(self, tmp_path):
        from agent.retrieval.file_reader import read_lines
        lines = ["line1\n", "line2\n", "line3\n"]
        f = _write(tmp_path / "test.txt", "".join(lines))
        result = read_lines(str(f), offset=0, limit=2)
        assert len(result) == 2
        assert "line1" in result[0]

    def test_read_lines_with_offset(self, tmp_path):
        from agent.retrieval.file_reader import read_lines
        content = "\n".join(f"line{i}" for i in range(10)) + "\n"
        f = _write(tmp_path / "test.txt", content)
        result = read_lines(str(f), offset=5, limit=3)
        assert len(result) == 3
        assert "line5" in result[0]

    def test_read_stream_yields_content(self, tmp_path):
        from agent.retrieval.file_reader import read_stream
        f = _write(tmp_path / "test.txt", "abc" * 1000)
        chunks = list(read_stream(str(f), chunk_size=512))
        assert len(chunks) > 0
        assert "abc" in "".join(chunks)

    def test_is_binary_text_file(self, tmp_path):
        from agent.retrieval.file_reader import is_binary
        f = _write(tmp_path / "test.py", "def foo(): pass")
        assert is_binary(str(f)) is False

    def test_is_binary_binary_file(self, tmp_path):
        from agent.retrieval.file_reader import is_binary
        f = _write_bytes(tmp_path / "test.bin", b"\x00\x01\x02\x03" * 64)
        assert is_binary(str(f)) is True

    def test_detect_encoding_utf8(self):
        from agent.retrieval.file_reader import detect_encoding
        assert detect_encoding(b"hello world") == "utf-8"

    def test_detect_encoding_ascii(self):
        from agent.retrieval.file_reader import detect_encoding
        assert detect_encoding(b"pure ascii") == "utf-8"

    def test_read_batch_multiple_files(self, tmp_path):
        from agent.retrieval.file_reader import read_batch
        files = []
        for i in range(5):
            f = _write(tmp_path / f"f{i}.txt", f"content {i}")
            files.append(str(f))
        results = read_batch(files)
        assert len(results) == 5
        for path, content in results.items():
            assert not isinstance(content, Exception)
            assert "content" in content

    def test_read_batch_handles_missing_file(self, tmp_path):
        from agent.retrieval.file_reader import read_batch
        f = _write(tmp_path / "exists.txt", "ok")
        results = read_batch([str(f), "/nonexistent/file.txt"])
        assert not isinstance(results[str(f)], Exception)
        assert isinstance(results["/nonexistent/file.txt"], Exception)

    def test_capabilities_keys(self):
        from agent.retrieval.file_reader import capabilities
        caps = capabilities()
        assert "mmap" in caps
        assert "mmap_threshold" in caps
        assert "stream_threshold" in caps

    def test_max_file_size_enforced(self, tmp_path, monkeypatch):
        from agent.retrieval import config
        # Temporarily reduce max size
        monkeypatch.setattr(config, "MAX_FILE_SIZE", 10)
        from agent.retrieval.file_reader import read_smart
        f = _write(tmp_path / "big.txt", "x" * 20)
        with pytest.raises(ValueError, match="MAX_FILE_SIZE"):
            read_smart(str(f))


# ---------------------------------------------------------------------------
# retrieval.fd_discovery
# ---------------------------------------------------------------------------


class TestFdDiscovery:
    def test_discover_files_returns_list(self, tmp_path):
        from agent.retrieval.fd_discovery import discover_files
        _write(tmp_path / "a.py", "x=1")
        _write(tmp_path / "b.py", "y=2")
        paths = discover_files(str(tmp_path), extensions=[".py"])
        assert any(p.endswith("a.py") for p in paths)
        assert any(p.endswith("b.py") for p in paths)

    def test_discover_files_respects_extension_filter(self, tmp_path):
        from agent.retrieval.fd_discovery import discover_files
        _write(tmp_path / "code.py", "x=1")
        _write(tmp_path / "data.json", "{}")
        paths = discover_files(str(tmp_path), extensions=[".py"])
        assert all(p.endswith(".py") for p in paths)

    def test_discover_files_sorted_by_mtime(self, tmp_path):
        from agent.retrieval.fd_discovery import discover_files
        old = _write(tmp_path / "old.py", "x=1")
        time.sleep(0.05)
        new = _write(tmp_path / "new.py", "y=2")
        paths = discover_files(str(tmp_path), extensions=[".py"])
        if len(paths) >= 2:
            # Newest first
            assert paths[0].endswith("new.py")

    def test_discover_files_max_results(self, tmp_path):
        from agent.retrieval.fd_discovery import discover_files
        for i in range(10):
            _write(tmp_path / f"f{i}.py", f"x={i}")
        paths = discover_files(str(tmp_path), extensions=[".py"], max_results=3)
        assert len(paths) <= 3

    def test_fd_available_is_bool(self):
        from agent.retrieval.fd_discovery import fd_available
        assert isinstance(fd_available(), bool)


# ---------------------------------------------------------------------------
# retrieval.fts_index
# ---------------------------------------------------------------------------


class TestFTSIndex:
    def test_build_and_search(self, tmp_path, monkeypatch):
        from agent.retrieval.fts_index import FTSIndex
        # Point index at a temp location
        import agent.retrieval.fts_index as fts_mod
        monkeypatch.setattr(fts_mod, "_INDEX_DIR", tmp_path / "fts")

        _write(tmp_path / "auth.py", "def authenticate_user(token): pass")
        _write(tmp_path / "models.py", "class User: pass")

        idx = FTSIndex(str(tmp_path))
        count = idx.build()
        assert count >= 2

        results = idx.search("authenticate_user")
        paths = [r[0] for r in results]
        assert any("auth.py" in p for p in paths)

    def test_search_empty_index_returns_empty(self, tmp_path, monkeypatch):
        from agent.retrieval.fts_index import FTSIndex
        import agent.retrieval.fts_index as fts_mod
        monkeypatch.setattr(fts_mod, "_INDEX_DIR", tmp_path / "fts")

        idx = FTSIndex(str(tmp_path))
        # Don't build — should return empty gracefully
        results = idx.search("anything")
        assert results == []

    def test_incremental_update(self, tmp_path, monkeypatch):
        from agent.retrieval.fts_index import FTSIndex
        import agent.retrieval.fts_index as fts_mod
        monkeypatch.setattr(fts_mod, "_INDEX_DIR", tmp_path / "fts")

        f = _write(tmp_path / "service.py", "def process_payment(): pass")
        idx = FTSIndex(str(tmp_path))
        idx.build()

        # Modify the file
        f.write_text("def process_payment(): return True")
        updated = idx.update([str(f)])
        assert updated == 1

        results = idx.search("process_payment")
        assert len(results) > 0

    def test_update_deleted_file(self, tmp_path, monkeypatch):
        from agent.retrieval.fts_index import FTSIndex
        import agent.retrieval.fts_index as fts_mod
        monkeypatch.setattr(fts_mod, "_INDEX_DIR", tmp_path / "fts")

        f = _write(tmp_path / "temp.py", "def temp_func(): pass")
        idx = FTSIndex(str(tmp_path))
        idx.build()

        os.unlink(str(f))
        updated = idx.update([str(f)])
        assert updated == 1

    def test_stats(self, tmp_path, monkeypatch):
        from agent.retrieval.fts_index import FTSIndex
        import agent.retrieval.fts_index as fts_mod
        monkeypatch.setattr(fts_mod, "_INDEX_DIR", tmp_path / "fts")

        _write(tmp_path / "a.py", "x=1")
        idx = FTSIndex(str(tmp_path))
        idx.build()

        stats = idx.stats()
        assert "indexed_files" in stats
        assert stats["indexed_files"] >= 1

    def test_fts_disabled_returns_empty(self, tmp_path, monkeypatch):
        from agent.retrieval.fts_index import FTSIndex
        from agent.retrieval import config
        monkeypatch.setattr(config, "ENABLE_FTS", False)
        import agent.retrieval.fts_index as fts_mod
        monkeypatch.setattr(fts_mod, "_INDEX_DIR", tmp_path / "fts")

        idx = FTSIndex(str(tmp_path))
        assert idx.build() == 0
        assert idx.search("anything") == []

    def test_sanitize_fts_query(self):
        from agent.retrieval.fts_index import _sanitize_fts_query
        # Should not raise for special chars
        result = _sanitize_fts_query("foo(bar)")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_sanitize_fts_boolean_query(self):
        from agent.retrieval.fts_index import _sanitize_fts_query
        result = _sanitize_fts_query("auth AND jwt")
        # Boolean operators should be preserved
        assert "AND" in result

    def test_context_manager(self, tmp_path, monkeypatch):
        from agent.retrieval.fts_index import FTSIndex
        import agent.retrieval.fts_index as fts_mod
        monkeypatch.setattr(fts_mod, "_INDEX_DIR", tmp_path / "fts")
        with FTSIndex(str(tmp_path)) as idx:
            assert idx is not None


# ---------------------------------------------------------------------------
# retrieval.ast_search
# ---------------------------------------------------------------------------


class TestAstSearch:
    def test_python_fallback_finds_functions(self, tmp_path):
        from agent.retrieval.ast_search import _python_fallback
        _write(tmp_path / "utils.py", "def helper():\n    pass\n")
        results = _python_fallback("function", str(tmp_path), max_results=20)
        assert any(r["kind"] in ("function", "method") for r in results)

    def test_python_fallback_finds_classes(self, tmp_path):
        from agent.retrieval.ast_search import _python_fallback
        _write(tmp_path / "model.py", "class Foo:\n    pass\n")
        results = _python_fallback("class", str(tmp_path), max_results=20)
        assert any(r["kind"] == "class" for r in results)

    def test_detect_language_python(self, tmp_path):
        from agent.retrieval.ast_search import _detect_language
        for i in range(5):
            _write(tmp_path / f"mod{i}.py", "x=1")
        assert _detect_language(str(tmp_path)) == "python"

    def test_detect_language_javascript(self, tmp_path):
        from agent.retrieval.ast_search import _detect_language
        for i in range(5):
            _write(tmp_path / f"mod{i}.js", "const x = 1")
        assert _detect_language(str(tmp_path)) in ("javascript", "typescript")

    def test_resolve_named_pattern_python(self):
        from agent.retrieval.ast_search import _resolve_pattern, _PYTHON_PATTERNS
        result = _resolve_pattern("function", "python")
        assert result == _PYTHON_PATTERNS["function"]

    def test_resolve_unknown_pattern_passthrough(self):
        from agent.retrieval.ast_search import _resolve_pattern
        raw_pattern = "$FUNC($$$ARGS)"
        result = _resolve_pattern(raw_pattern, "python")
        assert result == raw_pattern

    def test_search_returns_list(self, tmp_path):
        from agent.retrieval.ast_search import search
        _write(tmp_path / "code.py", "def foo(): pass\nclass Bar: pass\n")
        results = search("function", directory=str(tmp_path))
        assert isinstance(results, list)

    def test_available_is_bool(self):
        from agent.retrieval.ast_search import available
        assert isinstance(available(), bool)


# ---------------------------------------------------------------------------
# retrieval.graphify_adapter
# ---------------------------------------------------------------------------


class TestGraphifyAdapter:
    def test_adapter_fallback_to_ast(self, tmp_path):
        from agent.retrieval.graphify_adapter import GraphifyAdapter
        _write(tmp_path / "services.py", "def send_email(): pass\n")
        adapter = GraphifyAdapter(str(tmp_path))
        # Should not raise regardless of graphifyy availability
        results = adapter.query_related("send_email")
        assert isinstance(results, list)

    def test_get_callers_fallback(self, tmp_path):
        from agent.retrieval.graphify_adapter import GraphifyAdapter
        _write(tmp_path / "app.py", "from services import send_email\nsend_email()\n")
        adapter = GraphifyAdapter(str(tmp_path))
        results = adapter.get_callers("send_email")
        assert isinstance(results, list)

    def test_get_imports_fallback(self, tmp_path):
        from agent.retrieval.graphify_adapter import GraphifyAdapter
        _write(tmp_path / "main.py", "import os\nimport json\n")
        adapter = GraphifyAdapter(str(tmp_path))
        imports = adapter.get_imports(str(tmp_path / "main.py"))
        assert isinstance(imports, list)

    def test_status_returns_string(self, tmp_path):
        from agent.retrieval.graphify_adapter import GraphifyAdapter
        adapter = GraphifyAdapter(str(tmp_path))
        status = adapter.status()
        assert isinstance(status, str)

    def test_available_is_bool(self):
        from agent.retrieval.graphify_adapter import available
        assert isinstance(available(), bool)

    def test_normalise_list_of_dicts(self, tmp_path):
        from agent.retrieval.graphify_adapter import GraphifyAdapter
        adapter = GraphifyAdapter(str(tmp_path))
        raw = [{"path": "a.py", "kind": "ref"}, {"path": "b.py"}]
        result = adapter._normalise(raw)
        assert len(result) == 2
        assert result[0]["path"] == "a.py"

    def test_normalise_list_of_strings(self, tmp_path):
        from agent.retrieval.graphify_adapter import GraphifyAdapter
        adapter = GraphifyAdapter(str(tmp_path))
        result = adapter._normalise(["a.py", "b.py"])
        assert all("path" in r for r in result)

    def test_normalise_none(self, tmp_path):
        from agent.retrieval.graphify_adapter import GraphifyAdapter
        adapter = GraphifyAdapter(str(tmp_path))
        assert adapter._normalise(None) == []


# ---------------------------------------------------------------------------
# retrieval.engine
# ---------------------------------------------------------------------------


class TestQueryRouter:
    def test_keyword_classification(self):
        from agent.retrieval.engine import classify_query, QueryType
        assert classify_query("authenticate") == QueryType.SYMBOL
        assert classify_query("jwt token") == QueryType.KEYWORD

    def test_relational_classification(self):
        from agent.retrieval.engine import classify_query, QueryType
        assert classify_query("what calls authenticate") == QueryType.RELATIONAL
        assert classify_query("who imports this module") == QueryType.RELATIONAL

    def test_structural_classification(self):
        from agent.retrieval.engine import classify_query, QueryType
        assert classify_query("find all functions") == QueryType.STRUCTURAL
        assert classify_query("all async methods") == QueryType.STRUCTURAL

    def test_docs_classification(self):
        from agent.retrieval.engine import classify_query, QueryType
        assert classify_query("documentation for auth") == QueryType.DOCS
        assert classify_query("README example") == QueryType.DOCS

    def test_semantic_classification(self):
        from agent.retrieval.engine import classify_query, QueryType
        result = classify_query("how does token validation work in the auth middleware")
        assert result in (QueryType.SEMANTIC, QueryType.COMPLEX)

    def test_symbol_exact_name(self):
        from agent.retrieval.engine import classify_query, QueryType
        assert classify_query("MyClassName") == QueryType.SYMBOL
        assert classify_query("process_payment") == QueryType.SYMBOL

    def test_search_returns_string(self, tmp_path):
        from agent.retrieval.engine import search
        _write(tmp_path / "auth.py", "def authenticate(): pass")
        result = search("authenticate", path=str(tmp_path))
        assert isinstance(result, str)

    def test_search_with_explicit_engines(self, tmp_path):
        from agent.retrieval.engine import search
        _write(tmp_path / "code.py", "x = 1")
        result = search("x = 1", path=str(tmp_path), engines=["ripgrep"])
        assert isinstance(result, str)

    def test_search_unknown_engine_graceful(self, tmp_path):
        from agent.retrieval.engine import search
        _write(tmp_path / "code.py", "x = 1")
        result = search("x", path=str(tmp_path), engines=["nonexistent_engine"])
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# retrieval.watcher
# ---------------------------------------------------------------------------


class TestWatcher:
    def test_available_is_bool(self):
        from agent.retrieval.watcher import available
        assert isinstance(available(), bool)

    def test_get_watcher_returns_instance(self, tmp_path):
        from agent.retrieval.watcher import get_watcher
        w = get_watcher(str(tmp_path))
        assert w is not None

    def test_watcher_disabled_by_default(self, tmp_path):
        from agent.retrieval.watcher import get_watcher
        from agent.retrieval import config
        assert config.ENABLE_WATCHER is False
        w = get_watcher(str(tmp_path))
        # start() should return False when ENABLE_WATCHER=False
        assert w.start() is False

    def test_callbacks_registered(self, tmp_path):
        from agent.retrieval.watcher import RepoWatcher
        fired: list[set] = []
        w = RepoWatcher(str(tmp_path))
        w.on_change(lambda paths: fired.append(paths))
        w._notify({"/some/path.py"})
        assert len(fired) == 1
        assert "/some/path.py" in fired[0]


# ---------------------------------------------------------------------------
# repo_index with xxhash fingerprinting
# ---------------------------------------------------------------------------


class TestRepoIndexWithFingerprints:
    def test_build_index_creates_fingerprints(self, tmp_path, monkeypatch):
        import agent.repo_index as ri
        monkeypatch.setattr(ri, "_index", {})
        monkeypatch.setattr(ri, "_fingerprints", {})
        monkeypatch.setattr(ri, "_indexed_root", None)
        monkeypatch.setattr(ri, "_dirty_paths", set())

        _write(tmp_path / "mod.py", "def foo(): pass\n")
        ri.build_index(str(tmp_path))
        assert len(ri._fingerprints) > 0

    def test_incremental_skips_unchanged(self, tmp_path, monkeypatch):
        import agent.repo_index as ri
        monkeypatch.setattr(ri, "_index", {})
        monkeypatch.setattr(ri, "_fingerprints", {})
        monkeypatch.setattr(ri, "_indexed_root", None)
        monkeypatch.setattr(ri, "_dirty_paths", set())

        f = _write(tmp_path / "mod.py", "def bar(): pass\n")
        ri.build_index(str(tmp_path))
        fp_before = dict(ri._fingerprints)

        # Mark as dirty but don't change the file
        ri.invalidate(str(f))
        ri._ensure_fresh(str(tmp_path))

        # Fingerprint should be unchanged (file didn't change)
        assert ri._fingerprints.get(str(f)) == fp_before.get(str(f))

    def test_index_stats_has_fingerprinted_count(self, tmp_path, monkeypatch):
        import agent.repo_index as ri
        monkeypatch.setattr(ri, "_index", {})
        monkeypatch.setattr(ri, "_fingerprints", {})
        monkeypatch.setattr(ri, "_indexed_root", None)
        monkeypatch.setattr(ri, "_dirty_paths", set())

        _write(tmp_path / "a.py", "x=1")
        ri.build_index(str(tmp_path))
        stats = ri.index_stats()
        assert "fingerprinted_files" in stats
        assert stats["fingerprinted_files"] >= 1


# ---------------------------------------------------------------------------
# New tool functions
# ---------------------------------------------------------------------------


class TestFtsSearchTool:
    def test_returns_string(self, tmp_path, monkeypatch):
        import agent.retrieval.fts_index as fts_mod
        monkeypatch.setattr(fts_mod, "_INDEX_DIR", tmp_path / "fts")
        monkeypatch.setattr(fts_mod, "_instances", {})
        _write(tmp_path / "code.py", "def find_user(): pass")

        from agent.tools._search import fts_search
        result = fts_search("find_user", path=str(tmp_path))
        assert isinstance(result, str)

    def test_bad_args(self):
        from agent.tools._search import fts_search
        result = fts_search("")
        assert "[ERROR" in result

    def test_limit_clamped(self, tmp_path, monkeypatch):
        import agent.retrieval.fts_index as fts_mod
        monkeypatch.setattr(fts_mod, "_INDEX_DIR", tmp_path / "fts")
        monkeypatch.setattr(fts_mod, "_instances", {})
        from agent.tools._search import fts_search
        # Should not raise
        result = fts_search("x", path=str(tmp_path), limit=9999)
        assert isinstance(result, str)


class TestAstSearchTool:
    def test_returns_string(self, tmp_path):
        from agent.tools._search import ast_search
        _write(tmp_path / "code.py", "def foo(): pass\nclass Bar: pass\n")
        result = ast_search("function", path=str(tmp_path))
        assert isinstance(result, str)

    def test_bad_args(self):
        from agent.tools._search import ast_search
        result = ast_search("")
        assert "[ERROR" in result

    def test_with_language(self, tmp_path):
        from agent.tools._search import ast_search
        _write(tmp_path / "code.py", "def foo(): pass")
        result = ast_search("function", language="python", path=str(tmp_path))
        assert isinstance(result, str)


class TestGraphifyQueryTool:
    def test_returns_string(self, tmp_path):
        from agent.tools._search import graphify_query
        _write(tmp_path / "a.py", "def process(): pass")
        result = graphify_query("process", path=str(tmp_path))
        assert isinstance(result, str)

    def test_bad_args(self):
        from agent.tools._search import graphify_query
        result = graphify_query("")
        assert "[ERROR" in result

    def test_invalid_query_type(self, tmp_path):
        from agent.tools._search import graphify_query
        result = graphify_query("x", query_type="invalid", path=str(tmp_path))
        assert "[ERROR" in result

    def test_valid_query_types(self, tmp_path):
        from agent.tools._search import graphify_query
        _write(tmp_path / "a.py", "def foo(): pass")
        for qtype in ("semantic", "callers", "callees", "imports", "dependents"):
            result = graphify_query("foo", query_type=qtype, path=str(tmp_path))
            assert isinstance(result, str)


class TestSearchSmartTool:
    def test_returns_string(self, tmp_path):
        from agent.tools._search import search_smart
        _write(tmp_path / "code.py", "def authenticate(): pass")
        result = search_smart("authenticate", path=str(tmp_path))
        assert isinstance(result, str)

    def test_bad_args(self):
        from agent.tools._search import search_smart
        result = search_smart("")
        assert "[ERROR" in result

    def test_explicit_engines(self, tmp_path):
        from agent.tools._search import search_smart
        _write(tmp_path / "code.py", "x = 1")
        result = search_smart("x = 1", path=str(tmp_path), engines="ripgrep")
        assert isinstance(result, str)

    def test_with_multiple_engines(self, tmp_path):
        from agent.tools._search import search_smart
        _write(tmp_path / "code.py", "class Auth: pass")
        result = search_smart("class", path=str(tmp_path), engines="ripgrep,ast")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Backward-compatibility: existing tools still work unchanged
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_read_file_still_works(self, tmp_path):
        from agent.tools._file_ops import read_file
        f = _write(tmp_path / "test.py", "x = 1\ny = 2\n")
        result = read_file(str(f))
        assert "x = 1" in result
        assert "     1\t" in result  # line numbers present

    def test_grep_still_works(self, tmp_path):
        from agent.tools._search import grep
        _write(tmp_path / "code.py", "def authenticate(): pass")
        result = grep("authenticate", path=str(tmp_path), output_mode="files_with_matches")
        assert "code.py" in result

    def test_glob_files_still_works(self, tmp_path):
        from agent.tools._search import glob_files
        _write(tmp_path / "a.py", "x")
        _write(tmp_path / "b.py", "y")
        result = glob_files("*.py", path=str(tmp_path))
        assert ".py" in result

    def test_semantic_search_still_works(self, tmp_path):
        from agent.tools._search import semantic_search
        _write(tmp_path / "auth.py", "def authenticate(): pass")
        # May return UNSUPPORTED if sentence-transformers not installed — that's OK
        result = semantic_search("authenticate", top_k=3, directory=str(tmp_path))
        assert isinstance(result, str)

    def test_lookup_symbol_still_works(self, tmp_path):
        from agent.tools._search import lookup_symbol
        _write(tmp_path / "models.py", "class User:\n    def save(self): pass\n")
        result = lookup_symbol("User", directory=str(tmp_path))
        assert isinstance(result, str)

    def test_tool_registry_sanity_check_passes(self):
        """Verify that the tool registry / spec sanity check doesn't raise."""
        from agent.tools import TOOLS, TOOL_SPECS
        spec_names = {t["function"]["name"] for t in TOOL_SPECS}
        assert "fts_search" in spec_names
        assert "ast_search" in spec_names
        assert "graphify_query" in spec_names
        assert "search_smart" in spec_names
        assert "fts_search" in TOOLS
        assert "ast_search" in TOOLS
        assert "graphify_query" in TOOLS
        assert "search_smart" in TOOLS

    def test_read_only_tools_contains_new_tools(self):
        from agent.tools import READ_ONLY_TOOLS
        assert "fts_search" in READ_ONLY_TOOLS
        assert "ast_search" in READ_ONLY_TOOLS
        assert "graphify_query" in READ_ONLY_TOOLS
        assert "search_smart" in READ_ONLY_TOOLS


# ---------------------------------------------------------------------------
# retrieval package capabilities()
# ---------------------------------------------------------------------------


class TestRetrievalCapabilities:
    def test_capabilities_returns_dict(self):
        from agent.retrieval import capabilities
        caps = capabilities()
        assert isinstance(caps, dict)

    def test_capabilities_has_required_keys(self):
        from agent.retrieval import capabilities
        caps = capabilities()
        expected_keys = {
            "ripgrep", "fd", "ast_grep", "graphify",
            "fts", "mmap", "watcher_enabled", "watcher_available",
        }
        assert expected_keys.issubset(caps.keys())

    def test_capabilities_booleans(self):
        from agent.retrieval import capabilities
        caps = capabilities()
        for key in ("ripgrep", "fd", "ast_grep", "graphify", "fts", "mmap"):
            assert isinstance(caps[key], bool), f"{key} should be bool"


# ---------------------------------------------------------------------------
# Context.py enhancements
# ---------------------------------------------------------------------------


class TestContextEnhancements:
    def test_build_project_map_works(self, tmp_path):
        from agent.context import build_project_map
        _write(tmp_path / "main.py", "x=1")
        _write(tmp_path / "utils.py", "y=2")
        result = build_project_map(str(tmp_path))
        assert isinstance(result, str)
        assert "main.py" in result or "utils.py" in result

    def test_orjson_roundtrip_in_snapshot(self, tmp_path, monkeypatch):
        from agent.context import _json_dumps, _json_loads
        data = {"key": [1, 2, 3], "flag": True, "val": 3.14}
        serialised = _json_dumps(data)
        assert isinstance(serialised, str)
        recovered = _json_loads(serialised)
        assert recovered == data


# ---------------------------------------------------------------------------
# settings.py retrieval defaults
# ---------------------------------------------------------------------------


class TestSettingsRetrieval:
    def test_retrieval_defaults_present(self):
        from agent.settings import DEFAULTS
        assert "retrieval" in DEFAULTS
        rd = DEFAULTS["retrieval"]
        for key in (
            "ENABLE_GRAPHIFY", "ENABLE_FTS", "ENABLE_MMAP",
            "MMAP_THRESHOLD", "CACHE_TTL",
        ):
            assert key in rd, f"Missing key: {key}"

    def test_new_tools_in_allow_list(self):
        from agent.settings import DEFAULTS
        allowed = DEFAULTS["permissions"]["allow"]
        assert "fts_search:*" in allowed
        assert "ast_search:*" in allowed
        assert "graphify_query:*" in allowed
        assert "search_smart:*" in allowed

    def test_load_settings_merges_retrieval(self, tmp_path, monkeypatch):
        import agent.settings as settings_mod
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", str(tmp_path / "settings.json"))
        settings = settings_mod.load_settings()
        assert "retrieval" in settings
