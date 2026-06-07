"""Unified Language Server Protocol (LSP) integration layer.

Provides language-aware navigation capabilities:
- go_to_definition
- find_references
- hover
- workspace_symbols
- document_symbols
- rename
- implementations
- type_definitions

Features:
- Multi-language support (auto-start appropriate server)
- Persistent response caching
- Graceful fallback if server unavailable
- Timeout protection
- Integration with existing cache layer
- Non-blocking operations where possible
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.retrieval import config as cfg
from agent.retrieval.cache import get_cache, file_fingerprint


# ---------------------------------------------------------------------------
# LSP protocol constants
# ---------------------------------------------------------------------------

LSP_METHODS = {
    'initialize',
    'initialized',
    'shutdown',
    'exit',
    'textDocument/didOpen',
    'textDocument/didChange',
    'textDocument/didClose',
    'textDocument/definition',
    'textDocument/references',
    'textDocument/hover',
    'textDocument/documentSymbol',
    'workspace/symbol',
    'textDocument/rename',
    'textDocument/implementation',
    'textDocument/typeDefinition',
}

# Error codes
ERROR_CODE_REQUEST_CANCELLED = -32800
ERROR_CODE_SERVER_NOT_STARTED = -32801
ERROR_CODE_TIMEOUT = -32802


# ---------------------------------------------------------------------------
# Language server configurations
# ---------------------------------------------------------------------------

_LANGUAGE_SERVERS = {
    'python': {
        'command': ['pyright-langserver', '--stdio'],
        'file_extensions': ['.py'],
        'language_id': 'python',
    },
    'typescript': {
        'command': ['typescript-language-server', '--stdio'],
        'file_extensions': ['.ts', '.tsx', '.js', '.jsx'],
        'language_id': 'typescript',
    },
    'javascript': {
        'command': ['typescript-language-server', '--stdio'],
        'file_extensions': ['.js', '.jsx'],
        'language_id': 'javascript',
    },
    'go': {
        'command': ['gopls', 'serve'],
        'file_extensions': ['.go'],
        'language_id': 'go',
    },
    'rust': {
        'command': ['rust-analyzer'],
        'file_extensions': ['.rs'],
        'language_id': 'rust',
    },
    'java': {
        'command': ['jdtls'],  # Eclipse JDT LS - requires more setup
        'file_extensions': ['.java'],
        'language_id': 'java',
    },
    'c': {
        'command': ['clangd'],
        'file_extensions': ['.c', '.h'],
        'language_id': 'c',
    },
    'cpp': {
        'command': ['clangd'],
        'file_extensions': ['.cpp', '.hpp', '.cc', '.cxx', '.h'],
        'language_id': 'cpp',
    },
}


def _get_language_for_file(path: str) -> Optional[str]:
    """Determine language from file extension."""
    ext = os.path.splitext(path)[1].lower()
    for lang, config in _LANGUAGE_SERVERS.items():
        if ext in config['file_extensions']:
            return lang
    return None


# ---------------------------------------------------------------------------
# LSP Client Implementation
# ---------------------------------------------------------------------------

class LSPClient:
    """Manages a single language server process."""

    def __init__(self, language: str, config: Dict[str, Any]):
        self.language = language
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0
        self._lock = threading.RLock()
        self._responses: Dict[int, Tuple[Any, Optional[Exception]]] = {}
        self._condition = threading.Condition(self._lock)
        self._initialized = False
        self._root_uri = ""

    def start(self, root_uri: str) -> bool:
        """Start the language server process."""
        if self.process is not None:
            return True

        try:
            self.process = subprocess.Popen(
                self.config['command'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            self._root_uri = root_uri

            # Send initialize request
            init_params = {
                "processId": self.process.pid,
                "rootUri": root_uri,
                "capabilities": {
                    "textDocument": {
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "hover": {"dynamicRegistration": False},
                        "documentSymbol": {"dynamicRegistration": False},
                        "rename": {"dynamicRegistration": False},
                        "implementation": {"dynamicRegistration": False},
                        "typeDefinition": {"dynamicRegistration": False},
                    },
                    "workspace": {
                        "symbol": {"dynamicRegistration": False},
                    },
                },
            }

            response, error = self._send_request('initialize', init_params, timeout=10.0)
            if error is None and response and 'capabilities' in response:
                self._initialized = True
                # Send initialized notification
                self._send_notification('initialized', {})
                return True
            else:
                self.stop()
                return False
        except Exception:
            self.stop()
            return False

    def stop(self) -> None:
        """Stop the language server."""
        if self.process:
            try:
                self._send_notification('shutdown', {})
                self._send_notification('exit', {})
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                self.process.kill()
            finally:
                self.process = None
                self._initialized = False

    def _send_request(self, method: str, params: Any, timeout: float = 5.0) -> Tuple[Optional[Any], Optional[Exception]]:
        """Send a request and wait for response."""
        if not self.process or not self._initialized:
            return None, Exception("Server not initialized")

        with self._lock:
            req_id = self.request_id
            self.request_id += 1

            request = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }

            try:
                self.process.stdin.write((json.dumps(request) + "\n").encode())
                self.process.stdin.flush()
            except Exception as e:
                return None, e

        # Wait for response
        with self._condition:
            start_time = time.time()
            while req_id not in self._responses:
                if time.time() - start_time > timeout:
                    return None, Exception(f"Timeout after {timeout}s")
                self._condition.wait(timeout=max(0, timeout - (time.time() - start_time)))

            response, error = self._responses.pop(req_id)
            return response, error

    def _send_notification(self, method: str, params: Any) -> None:
        """Send a notification (no response expected)."""
        if not self.process or not self._initialized:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        try:
            self.process.stdin.write((json.dumps(notification) + "\n").encode())
            self.process.stdin.flush()
        except Exception:
            pass

    def _reader_loop(self) -> None:
        """Background thread: read responses from server."""
        if not self.process:
            return

        while True:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break
                message = json.loads(line.decode('utf-8', errors='ignore'))

                if 'id' in message:
                    req_id = message['id']
                    with self._lock:
                        if 'result' in message:
                            self._responses[req_id] = (message['result'], None)
                        elif 'error' in message:
                            self._responses[req_id] = (None, Exception(message['error'].get('message', 'Unknown error')))
                        self._condition.notify_all()
                # else: notification - ignore for now
            except Exception:
                break

    def did_open(self, path: str, text: str) -> None:
        """Notify server that a document is opened."""
        uri = self._path_to_uri(path)
        params = {
            "textDocument": {
                "uri": uri,
                "languageId": self.config['language_id'],
                "version": 1,
                "text": text,
            }
        }
        self._send_notification('textDocument/didOpen', params)

    def did_change(self, path: str, text: str) -> None:
        """Notify server of document change."""
        uri = self._path_to_uri(path)
        params = {
            "textDocument": {
                "uri": uri,
                "version": 2,  # Increment in real usage
            },
            "contentChanges": [
                {"text": text}
            ]
        }
        self._send_notification('textDocument/didChange', params)

    def did_close(self, path: str) -> None:
        """Notify server that document is closed."""
        uri = self._path_to_uri(path)
        params = {"textDocument": {"uri": uri}}
        self._send_notification('textDocument/didClose', params)

    def _path_to_uri(self, path: str) -> str:
        """Convert file path to LSP URI."""
        return f"file://{os.path.abspath(path)}"

    def _uri_to_path(self, uri: str) -> str:
        """Convert LSP URI to file path."""
        if uri.startswith('file://'):
            return uri[7:]
        return uri

    # -----------------------------------------------------------------------
    # Public API methods
    # -----------------------------------------------------------------------

    def go_to_definition(self, path: str, line: int, character: int) -> List[Dict[str, Any]]:
        """Go to symbol definition."""
        uri = self._path_to_uri(path)
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": character},  # LSP is 0-indexed
        }
        response, error = self._send_request('textDocument/definition', params, timeout=3.0)
        if error or not response:
            return []
        return self._parse_locations(response)

    def find_references(self, path: str, line: int, character: int) -> List[Dict[str, Any]]:
        """Find all references to a symbol."""
        uri = self._path_to_uri(path)
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": character},
        }
        response, error = self._send_request('textDocument/references', params, timeout=5.0)
        if error or not response:
            return []
        return self._parse_locations(response)

    def hover(self, path: str, line: int, character: int) -> Optional[Dict[str, Any]]:
        """Get hover information (type, docstring)."""
        uri = self._path_to_uri(path)
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": character},
        }
        response, error = self._send_request('textDocument/hover', params, timeout=3.0)
        if error or not response:
            return None
        return response

    def document_symbols(self, path: str) -> List[Dict[str, Any]]:
        """Get all symbols in a document."""
        uri = self._path_to_uri(path)
        params = {"textDocument": {"uri": uri}}
        response, error = self._send_request('textDocument/documentSymbol', params, timeout=5.0)
        if error or not response:
            return []
        return response if isinstance(response, list) else []

    def workspace_symbols(self, query: str) -> List[Dict[str, Any]]:
        """Search for symbols in the workspace."""
        params = {"query": query}
        response, error = self._send_request('workspace/symbol', params, timeout=10.0)
        if error or not response:
            return []
        return response if isinstance(response, list) else []

    def rename(self, path: str, line: int, character: int, new_name: str) -> List[Dict[str, Any]]:
        """Rename a symbol across the workspace."""
        uri = self._path_to_uri(path)
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": character},
            "newName": new_name,
        }
        response, error = self._send_request('textDocument/rename', params, timeout=10.0)
        if error or not response:
            return []
        # Response is a WorkspaceEdit with documentChanges
        return response.get('documentChanges', [])

    def implementations(self, path: str, line: int, character: int) -> List[Dict[str, Any]]:
        """Find implementations of an interface/abstract method."""
        uri = self._path_to_uri(path)
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": character},
        }
        response, error = self._send_request('textDocument/implementation', params, timeout=5.0)
        if error or not response:
            return []
        return self._parse_locations(response)

    def type_definitions(self, path: str, line: int, character: int) -> List[Dict[str, Any]]:
        """Find type definitions."""
        uri = self._path_to_uri(path)
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": character},
        }
        response, error = self._send_request('textDocument/typeDefinition', params, timeout=5.0)
        if error or not response:
            return []
        return self._parse_locations(response)

    def _parse_locations(self, response: Any) -> List[Dict[str, Any]]:
        """Parse LSP location results into a uniform format."""
        locations = []
        if isinstance(response, list):
            for item in response:
                if isinstance(item, dict):
                    uri = item.get('uri', '')
                    path = self._uri_to_path(uri)
                    range_data = item.get('range', {})
                    start = range_data.get('start', {})
                    line = start.get('line', 0) + 1  # Convert to 1-indexed
                    character = start.get('character', 0)
                    locations.append({
                        "path": path,
                        "line": line,
                        "character": character,
                        "uri": uri,
                    })
        return locations


# ---------------------------------------------------------------------------
# LSP Manager (multi-server coordination)
# ---------------------------------------------------------------------------

class LSPManager:
    """Manages multiple language servers with caching."""

    def __init__(self, root_dir: str = "."):
        self.root_dir = os.path.abspath(root_dir)
        self.root_uri = f"file://{self.root_dir}"
        self._clients: Dict[str, LSPClient] = {}
        self._cache = get_cache("lsp")
        self._lock = threading.RLock()
        self._reader_threads: List[threading.Thread] = []

    def _get_client(self, path: str) -> Optional[LSPClient]:
        """Get or start LSP client for the file's language."""
        language = _get_language_for_file(path)
        if not language:
            return None

        with self._lock:
            if language not in self._clients:
                config = _LANGUAGE_SERVERS.get(language)
                if not config:
                    return None
                client = LSPClient(language, config)
                if client.start(self.root_uri):
                    self._clients[language] = client
                    # Start reader thread
                    thread = threading.Thread(target=client._reader_loop, daemon=True)
                    thread.start()
                    self._reader_threads.append(thread)
                else:
                    return None
            return self._clients.get(language)

    def _cache_key(self, method: str, path: str, line: int = 0, character: int = 0, extra: str = "") -> str:
        """Build cache key for LSP response."""
        fp = file_fingerprint(path) if os.path.exists(path) else ""
        parts = [method, path, str(line), str(character), fp, extra]
        return ":".join(parts)

    # -----------------------------------------------------------------------
    # Public API (mirrors LSPClient but with caching)
    # -----------------------------------------------------------------------

    def go_to_definition(self, path: str, line: int, character: int) -> List[Dict[str, Any]]:
        """Go to symbol definition with caching."""
        if not cfg.ENABLE_LSP:
            return []

        cache_key = self._cache_key("go_to_definition", path, line, character)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        client = self._get_client(path)
        if client is None:
            return []

        result = client.go_to_definition(path, line, character)
        self._cache.set(cache_key, result, expire=cfg.CACHE_TTL)
        return result

    def find_references(self, path: str, line: int, character: int) -> List[Dict[str, Any]]:
        """Find all references to a symbol with caching."""
        if not cfg.ENABLE_LSP:
            return []

        cache_key = self._cache_key("find_references", path, line, character)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        client = self._get_client(path)
        if client is None:
            return []

        result = client.find_references(path, line, character)
        self._cache.set(cache_key, result, expire=cfg.CACHE_TTL)
        return result

    def hover(self, path: str, line: int, character: int) -> Optional[Dict[str, Any]]:
        """Get hover information with caching."""
        if not cfg.ENABLE_LSP:
            return None

        cache_key = self._cache_key("hover", path, line, character)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        client = self._get_client(path)
        if client is None:
            return None

        result = client.hover(path, line, character)
        self._cache.set(cache_key, result, expire=cfg.CACHE_TTL)
        return result

    def document_symbols(self, path: str) -> List[Dict[str, Any]]:
        """Get all symbols in a document with caching."""
        if not cfg.ENABLE_LSP:
            return []

        cache_key = self._cache_key("document_symbols", path)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        client = self._get_client(path)
        if client is None:
            return []

        result = client.document_symbols(path)
        self._cache.set(cache_key, result, expire=cfg.CACHE_TTL)
        return result

    def workspace_symbols(self, query: str) -> List[Dict[str, Any]]:
        """Search workspace symbols (no caching - too broad)."""
        if not cfg.ENABLE_LSP:
            return []

        # Try each client until we get results
        for client in self._clients.values():
            result = client.workspace_symbols(query)
            if result:
                return result
        return []

    def rename(self, path: str, line: int, character: int, new_name: str) -> List[Dict[str, Any]]:
        """Rename a symbol (no caching - write operation)."""
        if not cfg.ENABLE_LSP:
            return []

        client = self._get_client(path)
        if client is None:
            return []

        return client.rename(path, line, character, new_name)

    def implementations(self, path: str, line: int, character: int) -> List[Dict[str, Any]]:
        """Find implementations with caching."""
        if not cfg.ENABLE_LSP:
            return []

        cache_key = self._cache_key("implementations", path, line, character)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        client = self._get_client(path)
        if client is None:
            return []

        result = client.implementations(path, line, character)
        self._cache.set(cache_key, result, expire=cfg.CACHE_TTL)
        return result

    def type_definitions(self, path: str, line: int, character: int) -> List[Dict[str, Any]]:
        """Find type definitions with caching."""
        if not cfg.ENABLE_LSP:
            return []

        cache_key = self._cache_key("type_definitions", path, line, character)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        client = self._get_client(path)
        if client is None:
            return []

        result = client.type_definitions(path, line, character)
        self._cache.set(cache_key, result, expire=cfg.CACHE_TTL)
        return result

    def shutdown(self) -> None:
        """Shutdown all language servers."""
        with self._lock:
            for client in self._clients.values():
                try:
                    client.stop()
                except Exception:
                    pass
            self._clients.clear()

    def capabilities(self) -> Dict[str, Any]:
        """Report LSP capabilities."""
        return {
            "enabled": cfg.ENABLE_LSP,
            "available_servers": list(_LANGUAGE_SERVERS.keys()),
            "active_servers": list(self._clients.keys()),
            "has_cache": True,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: Optional[LSPManager] = None


def get_manager(directory: str = ".") -> LSPManager:
    """Get or create the LSP manager for a directory."""
    global _manager
    if _manager is None:
        _manager = LSPManager(directory)
    return _manager


def capabilities() -> Dict[str, Any]:
    """Return LSP capabilities."""
    return get_manager().capabilities()