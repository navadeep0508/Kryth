"""File operations: read, write, edit, multi_edit, delete, list.

All writes go through ``_atomic_write`` (write-temp → fsync → rename)
so a crash mid-write doesn't corrupt the target file. All errors flow
through the ``[ERROR <CODE>] ...`` convention in ``_results`` so the
model can reliably distinguish failure from a payload.
"""

from __future__ import annotations

import ast
import difflib
import json
import os
import tempfile

from agent import ui
from agent.context import IGNORE_DIRS
from agent.tools._results import err


def _clean_path(value) -> str:
    return str(value).strip().strip("\"'")


def _invalidate_indexes(*paths: str) -> None:
    """Tell the repo + retriever indexes that ``paths`` changed.

    Lazy-imported to avoid importing the (potentially heavy)
    ``retriever`` module unless the user has run a tool that mutates
    files. Failures are swallowed — index staleness is a soft signal,
    not an error path.
    """
    try:
        from agent import repo_index
        repo_index.invalidate(*paths)
    except Exception:
        pass
    try:
        from agent import retriever
        retriever.invalidate()
    except Exception:
        pass


def _checkpoint(path: str) -> None:
    """Snapshot ``path`` before mutating it. Side-channel — never raises."""
    try:
        from agent import snapshots
        snapshots.snapshot(path)
    except Exception:
        pass


DEFAULT_READ_LIMIT = 0  # 0 = no limit; use offset/limit args to paginate large files

# Lazy import for the retrieval file reader — avoids circular imports
# and keeps the module loadable even if the retrieval package is absent.
def _get_file_reader():
    try:
        from agent.retrieval import file_reader
        return file_reader
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _atomic_write(path: str, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    The new content lands in a temp file in the same directory; we
    fsync it to disk and ``os.replace`` it over the target. ``replace``
    is atomic on POSIX and on NTFS, so a partial write can never be
    observed by another process.

    Raises on any IO failure — callers wrap in their own try/except so
    the error code stays meaningful (``EXEC_FAILED``).
    """
    target_dir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(target_dir, exist_ok=True)

    base = os.path.basename(path) or "out"
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{base}.",
        suffix=".tmp",
        dir=target_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                # fsync is best-effort on some platforms (notably under
                # network filesystems). The atomic-replace below is the
                # real guarantee; fsync is the cherry on top.
                pass
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup on failure; swallow secondary errors so
        # the original exception is what propagates.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Edit-time match diagnostics
# ---------------------------------------------------------------------------

def _locate_matches(content: str, target: str) -> list[int]:
    """Return 1-based line numbers where each occurrence of ``target``
    starts. Used to tell the model where ambiguous matches sit."""
    out: list[int] = []
    if not target:
        return out
    start = 0
    while True:
        idx = content.find(target, start)
        if idx < 0:
            return out
        out.append(content.count("\n", 0, idx) + 1)
        start = idx + 1  # allow overlapping (rare but cheap)


def _suggest_near_matches(content: str, target: str, k: int = 3) -> str:
    """When ``target`` doesn't appear verbatim, surface the most similar
    lines so the model can repair its old_text. Empty string if nothing
    looks close enough — better to stay silent than mislead."""
    first_line = next((ln for ln in target.splitlines() if ln.strip()), "")
    if not first_line:
        return ""
    file_lines = content.splitlines()
    candidates = difflib.get_close_matches(first_line, file_lines, n=k, cutoff=0.6)
    if not candidates:
        return ""
    out: list[str] = []
    for c in candidates:
        try:
            ln = file_lines.index(c) + 1
        except ValueError:
            continue
        out.append(f"  line {ln}: {c[:160]}")
    return "\n".join(out)


def _ambiguity_error(
    *,
    path: str,
    target: str,
    matches: list[int],
    content: str,
    edit_index: int | None = None,
) -> str:
    """Compose the canonical AMBIGUOUS error for edit/multi-edit.

    ``matches`` is the list of 1-based line numbers; an empty list means
    "no match" and triggers the near-match suggestion; multiple matches
    triggers the "add more context" hint.
    """
    where = f" (edit {edit_index})" if edit_index is not None else ""

    if not matches:
        details = ""
        near = _suggest_near_matches(content, target)
        if near:
            details = "Possible near-matches:\n" + near
        return err(
            "AMBIGUOUS",
            f"old_text not found in {path}{where}",
            details,
        )

    if len(matches) > 1:
        listed = ", ".join(str(n) for n in matches[:8])
        more = f" (and {len(matches) - 8} more)" if len(matches) > 8 else ""
        details = (
            f"old_text matched at lines: {listed}{more}.\n"
            f"Add more surrounding lines to old_text so it identifies "
            f"a single location."
        )
        return err(
            "AMBIGUOUS",
            f"old_text matches {len(matches)} places in {path}{where}",
            details,
        )

    raise AssertionError("unreachable: _ambiguity_error called with one match")


# ---------------------------------------------------------------------------
# Content validation (post-write linting)
# ---------------------------------------------------------------------------

_LENGTH_FLOORS = {
    ".html": 4000, ".htm": 4000,
    ".css": 3000, ".scss": 3000, ".sass": 3000,
    ".js": 600, ".jsx": 600, ".ts": 600, ".tsx": 600,
    ".vue": 600, ".svelte": 600,
    ".py": 200, ".md": 400,
}


def _length_advisory(path: str, length: int) -> str:
    ext = os.path.splitext(path)[1].lower()
    floor = _LENGTH_FLOORS.get(ext)
    if not floor or length >= floor:
        return ""
    return (
        f"\n[advisory] {path} is only {length} chars (floor for {ext} "
        f"is ~{floor}). This usually means the file is a stub: missing "
        f"sections, missing styling, or placeholder content. If the "
        f"user asked for a real {ext.lstrip('.')} page/module, EXPAND "
        f"this file now — add the missing sections, real copy, proper "
        f"design tokens — before moving on."
    )


def _count_balance(text: str, pairs: list[tuple[str, str]]) -> list[str]:
    issues = []
    for opener, closer in pairs:
        o, c = text.count(opener), text.count(closer)
        if o != c:
            issues.append(f"{opener}/{closer}: {o} open vs {c} close")
    return issues


def _count_html_tags(text: str) -> list[str]:
    import re as _re
    issues = []
    for tag in ("div", "section", "header", "footer", "main", "nav",
                "article", "ul", "ol", "table", "tr", "td", "p", "form",
                "html", "head", "body"):
        opens = len(_re.findall(rf"<{tag}\b[^>]*>", text, _re.I))
        opens -= len(_re.findall(rf"<{tag}\b[^>]*/\s*>", text, _re.I))
        closes = len(_re.findall(rf"</{tag}\s*>", text, _re.I))
        if opens != closes:
            issues.append(f"<{tag}>: {opens} open vs {closes} close")
    return issues


def _validate_content(path: str, content: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".py":
            ast.parse(content)
            return ""
        if ext == ".json":
            json.loads(content)
            return ""
        if ext in (".html", ".htm"):
            issues = _count_html_tags(content)
            if issues:
                return "\n[validation] unbalanced HTML tags: " + "; ".join(issues[:4])
            return ""
        if ext in (".css", ".scss", ".sass"):
            issues = _count_balance(content, [("{", "}"), ("(", ")")])
            if issues:
                return "\n[validation] unbalanced CSS: " + "; ".join(issues)
            return ""
        if ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
            issues = _count_balance(content, [("{", "}"), ("(", ")"), ("[", "]")])
            if issues:
                return "\n[validation] unbalanced JS/TS: " + "; ".join(issues)
            return ""
    except SyntaxError as e:
        return f"\n[validation] {ext} syntax error: {e.msg} at line {e.lineno}"
    except json.JSONDecodeError as e:
        return f"\n[validation] JSON parse error: {e.msg} at line {e.lineno}"
    except Exception as e:
        return f"\n[validation] {ext} check failed ({type(e).__name__})"
    return ""


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

def _looks_binary(path: str) -> str | None:
    """Cheap pre-check before attempting to UTF-8 decode a file.

    Returns ``None`` if the file is probably text. Otherwise returns a
    short label describing what we detected (``"png"``, ``"pdf"``,
    ``"null-bytes"``, …) so callers can produce a helpful error.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(512)
    except OSError:
        return None  # let the regular read path produce the real error

    if not head:
        return None

    magic = {
        b"\x89PNG\r\n\x1a\n": "png",
        b"\xff\xd8\xff": "jpeg",
        b"GIF87a": "gif", b"GIF89a": "gif",
        b"%PDF-": "pdf",
        b"PK\x03\x04": "zip / docx / jar / xlsx",
        b"\x7fELF": "elf binary",
        b"MZ": "windows executable",
        b"\x1f\x8b": "gzip",
        b"BZh": "bzip2",
        b"\xfd7zXZ\x00": "xz",
        b"RIFF": "riff (wav/avi/webp)",
        b"OggS": "ogg",
        b"ID3": "mp3",
        b"\xca\xfe\xba\xbe": "java class",
    }
    for sig, label in magic.items():
        if head.startswith(sig):
            return label

    # Final heuristic: more than 1% null bytes in the head is a strong
    # binary signal that the magic table doesn't cover.
    if head.count(b"\x00") / max(1, len(head)) > 0.01:
        return "null-bytes"

    return None


def _extract_pdf_text(path: str) -> str | None:
    """Extract text from a PDF using whichever library is available.

    Tries PyMuPDF, pdfminer, pypdf, pdfplumber, and pdftotext CLI
    in order. Returns None if no PDF library is installed.
    """
    # PyMuPDF (fitz) — fastest, best quality
    try:
        import fitz
        doc = fitz.open(path)
        pages = [page.get_text() for page in doc]
        doc.close()
        return "\n".join(pages)
    except ImportError:
        pass
    except Exception:
        pass

    # pdfminer.six — widely available
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        return pdfminer_extract(path)
    except ImportError:
        pass
    except Exception:
        pass

    # pypdf — lightweight
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        pass
    except Exception:
        pass

    # pdfplumber — good for structured extraction
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except ImportError:
        pass
    except Exception:
        pass

    # pdftotext CLI (poppler-utils)
    import subprocess as _sp
    try:
        result = _sp.run(["pdftotext", path, "-"], capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except Exception:
        pass

    return None


def read_file(path, offset=0, limit=None):
    path = _clean_path(path)
    binary_kind = _looks_binary(path)
    if binary_kind == "pdf":
        pdf_text = _extract_pdf_text(path)
        if pdf_text is not None:
            lines = pdf_text.splitlines(keepends=True)
            # Continue to normal line-numbered read below
        else:
            return err(
                "UNSUPPORTED",
                f"{path} is a PDF with no text extractor available",
                "Install one of: pip install PyMuPDF pdfminer.six pypdf pdfplumber",
            )
    elif binary_kind is not None:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        return err(
            "UNSUPPORTED",
            f"{path} looks like a binary file ({binary_kind})",
            (
                f"size: {size if size >= 0 else 'unknown'} bytes. "
                f"read_file only handles UTF-8 text. Use external tools "
                f"or shell commands for binary inspection."
            ),
        )
    else:
        try:
            # Use mmap-aware reader for large files to avoid loading
            # everything into memory just to slice a window of lines.
            fr = _get_file_reader()
            if fr is not None:
                try:
                    file_size = os.path.getsize(path)
                    if file_size >= fr.cfg.MMAP_THRESHOLD:
                        # For large files: count total lines via a fast pass,
                        # then read just the requested window via mmap.
                        # We still need total for the "N more lines" hint,
                        # but we don't load all lines into memory.
                        with open(path, "rb") as _f:
                            total = _f.read().count(b"\n")
                        raw_lines = fr.read_lines(path, offset, limit if (limit and limit > 0) else None)
                        out = []
                        for i, line in enumerate(raw_lines, start=(offset or 0) + 1):
                            out.append(f"{i:6d}\t{line.rstrip(chr(10))}")
                        end = (offset or 0) + len(raw_lines)
                        if end < total:
                            out.append(f"...[{total - end} more lines; call with offset={end}]")
                        return "\n".join(out) if out else "(empty file)"
                except Exception:
                    pass  # fall through to standard read below

            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return err("NOT_FOUND", f"file not found: {path}")
        except UnicodeDecodeError as e:
            return err(
                "UNSUPPORTED",
                f"{path} is not UTF-8 text",
                f"decode failed: {e}",
            )
        except Exception as e:
            return err("EXEC_FAILED", f"could not read {path}", str(e))

    total = len(lines)
    if offset < 0:
        offset = 0
    if limit is None:
        limit = DEFAULT_READ_LIMIT

    if limit and limit > 0:
        selected = lines[offset:offset + limit]
    else:
        selected = lines[offset:]

    out = []
    for i, line in enumerate(selected, start=offset + 1):
        if line.endswith("\n"):
            line = line[:-1]
        out.append(f"{i:6d}\t{line}")

    end = offset + len(selected)
    if end < total:
        out.append(f"...[{total - end} more lines; call with offset={end}]")

    return "\n".join(out) if out else "(empty file)"


def write_file(path, content):
    path = _clean_path(path)
    _checkpoint(path)
    try:
        ui.write_preview(path, content)
        _atomic_write(path, content)
    except Exception as e:
        return err("EXEC_FAILED", f"could not write {path}", str(e))

    _invalidate_indexes(path)

    # Post-execution validation: confirm the file actually landed on disk with
    # the expected size, so the result message never claims success blindly.
    try:
        written = os.path.getsize(path)
    except OSError as e:
        return err("EXEC_FAILED", f"write reported ok but {path} is not on disk", str(e))

    msg = f"✓ File written: {path} ({len(content)} chars, {written} bytes on disk)"
    msg += _length_advisory(path, len(content))
    msg += _validate_content(path, content)
    return msg


def delete_file(path):
    path = _clean_path(path)
    _checkpoint(path)
    try:
        os.remove(path)
    except FileNotFoundError:
        return err("NOT_FOUND", f"file not found: {path}")
    except IsADirectoryError:
        return err("BAD_ARGS", f"{path} is a directory; delete_file targets files only")
    except Exception as e:
        return err("EXEC_FAILED", f"could not delete {path}", str(e))
    _invalidate_indexes(path)
    # Post-execution validation: confirm the file is actually gone.
    if os.path.exists(path):
        return err("EXEC_FAILED", f"delete reported ok but {path} still exists")
    return f"✓ Deleted: {path}"


def list_files(directory="."):
    directory = _clean_path(directory or ".")
    if not os.path.exists(directory):
        return err("NOT_FOUND", f"directory not found: {directory}")
    if not os.path.isdir(directory):
        return err("BAD_ARGS", f"not a directory: {directory}")

    result = []
    try:
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for file in files:
                result.append(os.path.join(root, file))
    except FileNotFoundError:
        return err("NOT_FOUND", f"directory not found: {directory}")
    except Exception as e:
        return err("EXEC_FAILED", f"could not list {directory}", str(e))
    return "\n".join(result) if result else "(empty directory)"


def edit_file(path, old_text, new_text):
    path = _clean_path(path)
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        return err("BAD_ARGS", "edit_file: old_text and new_text must be strings")
    if old_text == "":
        return err("BAD_ARGS", "edit_file: old_text must be non-empty")

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return err("NOT_FOUND", f"file not found: {path}")
    except UnicodeDecodeError as e:
        return err("UNSUPPORTED", f"{path} is not UTF-8 text", str(e))
    except Exception as e:
        return err("EXEC_FAILED", f"could not read {path}", str(e))

    matches = _locate_matches(content, old_text)
    if len(matches) != 1:
        return _ambiguity_error(
            path=path, target=old_text, matches=matches, content=content,
        )

    updated = content.replace(old_text, new_text, 1)
    if updated == content:
        return err(
            "AMBIGUOUS",
            f"edit is a no-op in {path}",
            "old_text and new_text produce identical content.",
        )

    diff_text = "\n".join(difflib.unified_diff(
        content.splitlines(), updated.splitlines(),
        fromfile="before", tofile="after", lineterm="",
    ))
    ui.diff(diff_text, path=path)

    _checkpoint(path)
    try:
        _atomic_write(path, updated)
    except Exception as e:
        return err("EXEC_FAILED", f"could not write {path}", str(e))

    _invalidate_indexes(path)
    return f"Edited file: {path}" + _validate_content(path, updated)


def rollback_file(path, index: int = 0, list_only: bool = False):
    """Restore a file from its snapshot store (or list available snapshots).

    Snapshots are written automatically before every write_file /
    edit_file / multi_edit / delete_file. ``index=0`` is the most recent
    backup. Pass ``list_only=True`` to inspect what's available without
    restoring.
    """
    from agent import snapshots

    if list_only:
        items = snapshots.list_snapshots(path)
        if not items:
            return f"(no snapshots available for {path})"
        import time as _t
        lines = [f"snapshots for {path}:"]
        for it in items:
            ts = _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(it["ts"]))
            lines.append(f"  [{it['index']}]  {ts}  ({it['size']} bytes)")
        return "\n".join(lines)

    try:
        idx = int(index)
    except (TypeError, ValueError):
        return err("BAD_ARGS", "rollback_file: index must be an integer")

    ok, msg = snapshots.restore(path, idx)
    if not ok:
        return err("NOT_FOUND" if "no snapshots" in msg else "EXEC_FAILED", msg)
    return msg


def multi_edit(path, edits):
    if not isinstance(edits, list) or not edits:
        return err("BAD_ARGS", "multi_edit: edits must be a non-empty list")

    try:
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()
    except FileNotFoundError:
        return err("NOT_FOUND", f"file not found: {path}")
    except UnicodeDecodeError as e:
        return err("UNSUPPORTED", f"{path} is not UTF-8 text", str(e))
    except Exception as e:
        return err("EXEC_FAILED", f"could not read {path}", str(e))

    working = original
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return err("BAD_ARGS", f"multi_edit: edit {i} must be an object")
        old = edit.get("old_text")
        new = edit.get("new_text")
        if not isinstance(old, str) or not isinstance(new, str):
            return err(
                "BAD_ARGS",
                f"multi_edit: edit {i} missing or non-string old_text/new_text",
            )
        if old == "":
            return err("BAD_ARGS", f"multi_edit: edit {i} old_text must be non-empty")

        matches = _locate_matches(working, old)
        if len(matches) != 1:
            return _ambiguity_error(
                path=path, target=old, matches=matches,
                content=working, edit_index=i,
            )
        working = working.replace(old, new, 1)

    if working == original:
        return err(
            "AMBIGUOUS",
            f"multi_edit is a no-op in {path}",
            "All edits combined produced the original content.",
        )

    diff_text = "\n".join(difflib.unified_diff(
        original.splitlines(), working.splitlines(),
        fromfile="before", tofile="after", lineterm="",
    ))
    ui.diff(diff_text, path=path, title=f"multi-edit · {len(edits)} edits")

    _checkpoint(path)
    try:
        _atomic_write(path, working)
    except Exception as e:
        return err("EXEC_FAILED", f"could not write {path}", str(e))

    _invalidate_indexes(path)
    return (
        f"Applied {len(edits)} edits to {path}"
        + _validate_content(path, working)
    )
