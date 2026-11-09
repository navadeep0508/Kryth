from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from tools.base import (
    FileDiff,
    Tool,
    ToolConfirmation,
    ToolInvocation,
    ToolKind,
    ToolResult,
)
from utils.paths import ensure_parent_dir, resolve_path


class PatchAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    RENAME = "rename"


@dataclass
class PatchHunk:
    search: str
    replace: str


@dataclass
class PatchOperation:
    action: PatchAction
    path: Path
    content: str | None = None  # For create
    hunks: list[PatchHunk] = field(default_factory=list)  # For update
    move_path: Path | None = None  # Source for renames


@dataclass
class ParsedPatch:
    operations: list[PatchOperation]
    errors: list[str]


@dataclass
class PlannedOperation:
    """An operation checked against the (running) state of the working tree."""

    operation: PatchOperation
    description: str
    diff: FileDiff | None = None


class _WorkingTree:
    """
    File contents as the patch sees them, so operations compose: a file that an
    earlier hunk rewrote is read back rewritten, and a file created earlier in
    the patch can be renamed later on.
    """

    def __init__(self) -> None:
        self._pending: dict[Path, str | None] = {}

    def exists(self, path: Path) -> bool:
        if path in self._pending:
            return self._pending[path] is not None
        return path.exists()

    def read(self, path: Path) -> str:
        if path in self._pending:
            return self._pending[path] or ""
        return path.read_text(encoding="utf-8")

    def write(self, path: Path, content: str) -> None:
        self._pending[path] = content

    def remove(self, path: Path) -> None:
        self._pending[path] = None


class ApplyPatchParams(BaseModel):
    patch: str = Field(..., description="The patch content in the specified format")
    dry_run: bool = Field(
        False, description="Preview changes without applying them (default: false)"
    )


class ApplyPatchTool(Tool):
    """
    Supports a simple patch format:

    ```
    *** Begin Patch
    *** Update File: path/to/file.py
    <<<<<<< SEARCH
    old content to find
    =======
    new content to replace with
    >>>>>>> REPLACE
    *** End Patch
    ```

    Also supports:
    - *** Create File: path/to/new/file.py
    - *** Delete File: path/to/file.py
    - *** Rename File: old/path.py -> new/path.py
    """

    name = "apply_patch"
    description = (
        "Apply a multi-file patch. Supports creating, updating, deleting, and "
        "renaming files in a single operation. **PREFERRED** when editing 2 or more files "
        "instead of making multiple separate edit calls. More efficient and allows "
        "batching multiple file operations atomically: every operation is checked "
        "first, and nothing is written unless all of them can be applied.\n\n"
        "Format:\n"
        "*** Begin Patch\n"
        "*** Update File: path/to/file.py\n"
        "<<<<<<< SEARCH\n"
        "old content\n"
        "=======\n"
        "new content\n"
        ">>>>>>> REPLACE\n"
        "*** End Patch\n\n"
        "Also supports:\n"
        "*** Create File: path - creates new file with content after it\n"
        "*** Delete File: path - deletes the file\n"
        "*** Rename File: old -> new - renames/moves a file\n\n"
        "A single Update File may carry several SEARCH/REPLACE blocks, applied in order. "
        "Each search text must match exactly ( including whitespace and indentation ) "
        "and must be unique in the file. Set dry_run to preview without writing."
    )
    kind = ToolKind.WRITE
    schema = ApplyPatchParams  # type: ignore

    PATCH_START = re.compile(r"^\*\*\*\s*Begin\s+Patch\s*$", re.IGNORECASE)
    PATCH_END = re.compile(r"^\*\*\*\s*End\s+Patch\s*$", re.IGNORECASE)
    UPDATE_FILE = re.compile(r"^\*\*\*\s*Update\s+File:\s*(.+)$", re.IGNORECASE)
    CREATE_FILE = re.compile(r"^\*\*\*\s*Create\s+File:\s*(.+)$", re.IGNORECASE)
    DELETE_FILE = re.compile(r"^\*\*\*\s*Delete\s+File:\s*(.+)$", re.IGNORECASE)
    RENAME_FILE = re.compile(r"^\*\*\*\s*Rename\s+File:\s*(.+?)\s*->\s*(.+)$", re.IGNORECASE)

    SEARCH_START = re.compile(r"^<{7}\s*SEARCH\s*$")
    SEPARATOR = re.compile(r"^={7}\s*$")
    REPLACE_END = re.compile(r"^>{7}\s*REPLACE\s*$")

    def _is_directive(self, stripped: str) -> bool:
        return bool(
            self.UPDATE_FILE.match(stripped)
            or self.CREATE_FILE.match(stripped)
            or self.DELETE_FILE.match(stripped)
            or self.RENAME_FILE.match(stripped)
            or self.PATCH_END.match(stripped)
            or self.PATCH_START.match(stripped)
        )

    def _resolve(self, cwd: Path, raw: str) -> tuple[Path | None, str | None]:
        try:
            return resolve_path(cwd, raw.strip()), None
        except ValueError as e:
            return None, str(e)

    def _parse_patch(self, patch_text: str, cwd: Path) -> ParsedPatch:
        operations: list[PatchOperation] = []
        errors: list[str] = []

        lines = patch_text.splitlines()
        i = 0

        while i < len(lines):
            if self.PATCH_START.match(lines[i].strip()):
                i += 1
                break
            i += 1
        else:
            i = 0

        while i < len(lines):
            line = lines[i].strip()

            if self.PATCH_END.match(line):
                break

            if not line:
                i += 1
                continue

            if match := self.UPDATE_FILE.match(line):
                path, err = self._resolve(cwd, match.group(1))
                i += 1
                op, i, parse_err = self._parse_update(lines, i, path or Path(match.group(1)))
                if err:
                    errors.append(err)
                elif parse_err:
                    errors.append(parse_err)
                elif op:
                    operations.append(op)

            elif match := self.CREATE_FILE.match(line):
                path, err = self._resolve(cwd, match.group(1))
                i += 1
                content, i = self._read_until_next_operation(lines, i)
                if err:
                    errors.append(err)
                elif path:
                    operations.append(
                        PatchOperation(
                            action=PatchAction.CREATE,
                            path=path,
                            content=content,
                        )
                    )

            elif match := self.DELETE_FILE.match(line):
                path, err = self._resolve(cwd, match.group(1))
                if err:
                    errors.append(err)
                elif path:
                    operations.append(
                        PatchOperation(
                            action=PatchAction.DELETE,
                            path=path,
                        )
                    )
                i += 1

            elif match := self.RENAME_FILE.match(line):
                old_path, old_err = self._resolve(cwd, match.group(1))
                new_path, new_err = self._resolve(cwd, match.group(2))
                if old_err or new_err:
                    errors.append(old_err or new_err or "")
                elif old_path and new_path:
                    operations.append(
                        PatchOperation(
                            action=PatchAction.RENAME,
                            path=new_path,
                            move_path=old_path,
                        )
                    )
                i += 1

            else:
                i += 1

        return ParsedPatch(operations=operations, errors=errors)

    def _parse_update(
        self,
        lines: list[str],
        start: int,
        path: Path,
    ) -> tuple[PatchOperation | None, int, str | None]:
        """Parse one or more search/replace blocks belonging to an update."""

        hunks: list[PatchHunk] = []
        i = start

        while i < len(lines):
            stripped = lines[i].strip()

            if self._is_directive(stripped):
                break

            if not stripped:
                i += 1
                continue

            if not self.SEARCH_START.match(stripped):
                if hunks:
                    break
                # Tolerate prose between the header and the first block.
                i += 1
                continue

            i += 1

            search_lines: list[str] = []
            while i < len(lines) and not self.SEPARATOR.match(lines[i].strip()):
                if self._is_directive(lines[i].strip()):
                    return None, i, f"Missing ======= separator for {path}"
                search_lines.append(lines[i])
                i += 1
            if i >= len(lines):
                return None, i, f"Missing ======= separator for {path}"
            i += 1

            replace_lines: list[str] = []
            while i < len(lines) and not self.REPLACE_END.match(lines[i].strip()):
                if self._is_directive(lines[i].strip()):
                    return None, i, f"Missing >>>>>>> REPLACE for {path}"
                replace_lines.append(lines[i])
                i += 1
            if i >= len(lines):
                return None, i, f"Missing >>>>>>> REPLACE for {path}"
            i += 1

            hunks.append(
                PatchHunk(
                    search="\n".join(search_lines),
                    replace="\n".join(replace_lines),
                )
            )

        if not hunks:
            return None, i, f"Missing <<<<<<< SEARCH for {path}"

        return (
            PatchOperation(
                action=PatchAction.UPDATE,
                path=path,
                hunks=hunks,
            ),
            i,
            None,
        )

    def _read_until_next_operation(
        self,
        lines: list[str],
        start: int,
    ) -> tuple[str, int]:
        """Read content until the next operation directive."""
        content_lines = []
        i = start

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if (
                self.UPDATE_FILE.match(stripped)
                or self.CREATE_FILE.match(stripped)
                or self.DELETE_FILE.match(stripped)
                or self.RENAME_FILE.match(stripped)
                or self.PATCH_END.match(stripped)
            ):
                break

            content_lines.append(line)
            i += 1

        # Blank lines before the next directive are spacing in the patch, not
        # content; the file still gets the usual trailing newline back.
        while content_lines and not content_lines[-1].strip():
            content_lines.pop()

        if not content_lines:
            return "", i

        return "\n".join(content_lines) + "\n", i

    def _plan(
        self,
        operations: list[PatchOperation],
    ) -> tuple[list[PlannedOperation], list[str]]:
        """
        Check every operation against the working tree before anything is written,
        so a patch that cannot fully apply does not leave the files half-patched.
        """

        tree = _WorkingTree()
        planned: list[PlannedOperation] = []
        errors: list[str] = []

        for index, op in enumerate(operations, 1):
            prefix = f"Operation {index} ({op.action.value})"

            if op.action == PatchAction.CREATE:
                if tree.exists(op.path):
                    errors.append(
                        f"{prefix}: {op.path} already exists. "
                        "Use *** Update File to change it, or delete it first."
                    )
                    continue

                content = op.content or ""
                tree.write(op.path, content)
                planned.append(
                    PlannedOperation(
                        operation=op,
                        description=f"Create: {op.path}",
                        diff=FileDiff(
                            path=op.path,
                            old_content="",
                            new_content=content,
                            is_new_file=True,
                        ),
                    )
                )

            elif op.action == PatchAction.UPDATE:
                if not tree.exists(op.path):
                    errors.append(f"{prefix}: {op.path} does not exist")
                    continue

                try:
                    old_content = tree.read(op.path)
                except OSError as e:
                    errors.append(f"{prefix}: failed to read {op.path}: {e}")
                    continue

                new_content = old_content
                failed = False

                for hunk_index, hunk in enumerate(op.hunks, 1):
                    hunk_label = f"{prefix}, hunk {hunk_index} of {op.path}"

                    if not hunk.search:
                        errors.append(
                            f"{hunk_label}: empty SEARCH block. "
                            "Use *** Create File to add a new file."
                        )
                        failed = True
                        break

                    occurrences = new_content.count(hunk.search)

                    if occurrences == 0:
                        errors.append(
                            f"{hunk_label}: search content not found. "
                            "It must match exactly, including whitespace and indentation."
                        )
                        failed = True
                        break

                    if occurrences > 1:
                        errors.append(
                            f"{hunk_label}: search content found {occurrences} times. "
                            "Add surrounding context so the match is unique."
                        )
                        failed = True
                        break

                    new_content = new_content.replace(hunk.search, hunk.replace, 1)

                if failed:
                    continue

                if new_content == old_content:
                    errors.append(f"{prefix}: no change made to {op.path}")
                    continue

                tree.write(op.path, new_content)
                hunk_count = len(op.hunks)
                planned.append(
                    PlannedOperation(
                        operation=op,
                        description=f"Update: {op.path} ({hunk_count} hunk(s))",
                        diff=FileDiff(
                            path=op.path,
                            old_content=old_content,
                            new_content=new_content,
                        ),
                    )
                )

            elif op.action == PatchAction.DELETE:
                if not tree.exists(op.path):
                    errors.append(f"{prefix}: {op.path} does not exist")
                    continue

                try:
                    old_content = tree.read(op.path)
                except OSError:
                    old_content = ""

                tree.remove(op.path)
                planned.append(
                    PlannedOperation(
                        operation=op,
                        description=f"Delete: {op.path}",
                        diff=FileDiff(
                            path=op.path,
                            old_content=old_content,
                            new_content="",
                            is_deletion=True,
                        ),
                    )
                )

            elif op.action == PatchAction.RENAME:
                if not op.move_path or not tree.exists(op.move_path):
                    errors.append(f"{prefix}: source file {op.move_path} does not exist")
                    continue

                if tree.exists(op.path):
                    errors.append(f"{prefix}: target file {op.path} already exists")
                    continue

                try:
                    content = tree.read(op.move_path)
                except OSError:
                    content = ""

                tree.remove(op.move_path)
                tree.write(op.path, content)
                planned.append(
                    PlannedOperation(
                        operation=op,
                        description=f"Rename: {op.move_path} -> {op.path}",
                    )
                )

            else:
                errors.append(f"{prefix}: unknown action {op.action}")

        return planned, errors

    async def get_confirmation(
        self,
        invocation: ToolInvocation,
    ) -> ToolConfirmation | None:
        try:
            params = ApplyPatchParams(**invocation.params)
        except Exception:
            return None

        parsed = self._parse_patch(params.patch, invocation.cwd)

        if parsed.errors or not parsed.operations:
            return None

        planned, errors = self._plan(parsed.operations)

        if errors:
            return None

        affected_paths: list[Path] = []
        for item in planned:
            affected_paths.append(item.operation.path)
            if item.operation.move_path:
                affected_paths.append(item.operation.move_path)

        descriptions = [item.description for item in planned]
        diffs = [item.diff for item in planned if item.diff is not None]

        return ToolConfirmation(
            tool_name=self.name,
            params=invocation.params,
            description="\n".join(descriptions) if descriptions else "Apply patch",
            # A confirmation shows one diff; with several files the description
            # above is the summary and the diffs land in the result.
            diff=diffs[0] if len(diffs) == 1 else None,
            affected_paths=affected_paths,
            is_dangerous=any(
                item.operation.action == PatchAction.DELETE for item in planned
            ),
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        try:
            params = ApplyPatchParams(**invocation.params)
        except Exception as e:
            return ToolResult.error_result(f"Invalid parameters: {e}")

        parsed = self._parse_patch(params.patch, invocation.cwd)

        if parsed.errors:
            return ToolResult.error_result(
                "Patch parsing errors:\n" + "\n".join(f"- {e}" for e in parsed.errors)
            )

        if not parsed.operations:
            return ToolResult.error_result("No operations found in patch")

        planned, errors = self._plan(parsed.operations)

        if errors:
            return ToolResult.error_result(
                "Patch not applied - nothing was written:\n"
                + "\n".join(f"- {e}" for e in errors)
            )

        if params.dry_run:
            return ToolResult.success_result(
                f"[DRY RUN] Patch applies cleanly, {len(planned)} operation(s):\n"
                + "\n".join(f"- {item.description}" for item in planned),
                diff=planned[0].diff if len(planned) == 1 else None,
                metadata={
                    "operations": len(planned),
                    "dry_run": True,
                },
            )

        applied: list[str] = []

        for item in planned:
            try:
                self._apply(item.operation)
            except OSError as e:
                message = f"Failed at {item.description}: {e}"
                if applied:
                    message += "\n\nAlready applied before the failure:\n" + "\n".join(
                        f"- {done}" for done in applied
                    )
                return ToolResult.error_result(
                    message,
                    metadata={
                        "operations": len(planned),
                        "applied": len(applied),
                    },
                )

            applied.append(item.description)

        diffs = [item.diff for item in planned if item.diff is not None]
        paths = {str(item.operation.path) for item in planned}

        metadata: dict = {
            "operations": len(applied),
            "files": len(paths),
            "dry_run": False,
        }
        if len(paths) == 1:
            # The renderers use this to pick syntax highlighting for the diff.
            metadata["path"] = next(iter(paths))

        return ToolResult.success_result(
            f"Applied patch with {len(applied)} operation(s):\n"
            + "\n".join(f"- {item}" for item in applied),
            diff=diffs[0] if len(diffs) == 1 else None,
            metadata=metadata,
        )

    def _apply(self, op: PatchOperation) -> None:
        if op.action == PatchAction.CREATE:
            ensure_parent_dir(op.path)
            op.path.write_text(op.content or "", encoding="utf-8")

        elif op.action == PatchAction.UPDATE:
            content = op.path.read_text(encoding="utf-8")
            for hunk in op.hunks:
                content = content.replace(hunk.search, hunk.replace, 1)
            op.path.write_text(content, encoding="utf-8")

        elif op.action == PatchAction.DELETE:
            op.path.unlink()

        elif op.action == PatchAction.RENAME and op.move_path:
            ensure_parent_dir(op.path)
            op.move_path.rename(op.path)
