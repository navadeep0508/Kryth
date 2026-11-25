from pydantic import BaseModel, Field
from tools.base import FileDiff, Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult
from tools.core.code_integrity import CodeIntegrityValidator
from utils.paths import ensure_parent_dir, resolve_path

class WriteFileParams(BaseModel):
    path: str = Field(
        ...,
        description='Path to the file to write, relative to the working or absolute path.'
    )
    content: str = Field(
        ...,
        description="Content to write to a file."
    )
    create_directories: bool = Field(
        True,
        description='Create parent directories if they are yet to exist.'
    )
    overwrite: bool = Field(
        False,
        description='Explicitly confirm overwriting an existing file. '
                    'Set to true to replace an existing file; '
                    'use the edit tool for partial modifications instead.'
    )

class WriteFileTool(Tool):
    name = 'write'
    description = (
        "Write contents to a file, creates a file if it doesn't exist, "
        "or overwrites the file if it does. Parent directories are created automatically."
        "Use this tool for creating new files or replacing file contents."
        "For partial or smaller modifications, use the edit tool instead."
    )
    kind = ToolKind.WRITE
    schema = WriteFileParams

    async def get_confirmation(self, invocation: ToolInvocation) -> ToolConfirmation:
        params = WriteFileParams(**invocation.params)
        path = resolve_path(invocation.cwd, params.path)

        is_new_file = not path.exists()

        old_content = ""

        if not is_new_file:
            try:
                old_content = path.read_text(
                    encoding='utf-8'
                )
            except OSError:
                pass

        diff = FileDiff(
            path=path,
            old_content=old_content,
            new_content=params.content,
            is_new_file=is_new_file
        )

        action = "Create" if is_new_file else 'Overwrite'

        # No silent overwrites: require explicit overwrite confirmation.
        confirmation = ToolConfirmation(
            tool_name=self.name,
            params=invocation.params,
            description=f"{action} file: {path}",
            diff=diff,
            affected_paths=[path],
        )
        if not is_new_file and not params.overwrite:
            confirmation.description = (
                f"Refuse overwrite: {path} exists. Set overwrite=true to replace, "
                "or use the edit tool for partial modifications."
            )
            confirmation.is_dangerous = True
        return confirmation

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = WriteFileParams(**invocation.params)
        path = resolve_path(invocation.cwd, params.path)

        # Code integrity validation: check before writing.
        validator = CodeIntegrityValidator(invocation.cwd)
        pre_report = validator.validate_write(path, params.content, allow_overwrite=params.overwrite)
        if not pre_report.is_valid:
            error_msgs = [i.message for i in pre_report.errors]
            summary = error_msgs[0] if error_msgs else "Validation failed"
            return ToolResult.error_result(
                f"Code integrity check failed: {summary}"
                + ("\n" + "\n".join(error_msgs[1:]) if len(error_msgs) > 1 else ""),
                metadata={
                    "integrity_report": pre_report.to_dict(),
                    "path": str(path),
                },
            )

        is_new_file = not path.exists()
        old_content = ""

        if not is_new_file:
            try:
                old_content = path.read_text(
                    encoding='utf-8'
                )
            except:
                pass

        # No silent overwrites: refuse to overwrite without explicit confirmation.
        if not is_new_file and not params.overwrite:
            return ToolResult.error_result(
                f"File already exists: {path}. "
                f"Set overwrite=true to explicitly replace it, "
                f"or use the edit tool for partial modifications.",
                metadata={
                    "path": str(path),
                    "overwrite_required": True,
                    "integrity_report": pre_report.to_dict(),
                },
            )

        try:
            if params.create_directories:
                ensure_parent_dir(path)
            elif not path.parent.exists():
                return ToolResult.error_result(f'Parent directory does not exist: {path.parent}')
            path.write_text(params.content, encoding='utf-8')

            # Code integrity validation: read-back verification after writing.
            post_report = validator.verify_write(path, params.content)
            if not post_report.is_valid:
                error_msgs = [i.message for i in post_report.errors]
                return ToolResult.error_result(
                    "Read-back verification failed:\n" + "\n".join(error_msgs),
                    metadata={
                        "integrity_report": post_report.to_dict(),
                        "path": str(path),
                    },
                )

            action = "Created" if is_new_file else 'Updated'
            line_count = len(params.content.splitlines())

            return ToolResult.success_result(
                f"{action} {path} {line_count} lines",
                diff=FileDiff(
                    path=path,
                    old_content=old_content,
                    new_content=params.content,
                    is_new_file=is_new_file
                ),
                metadata = {
                    'path': str(path),
                    'is_new_file': is_new_file,
                    'lines': line_count,
                    'bytes': len(params.content.encode(
                        'utf-8'
                    )),
                    'integrity_report': pre_report.to_dict(),
                }
            )

        except OSError as e:
            return ToolResult.error_result(f"Failed to write file: {e}")
        
