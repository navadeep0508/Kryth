"""Context module for conversation management."""

from context.manager import ContextManager, MessageItem
from context.project_context import (
    ProjectContext,
    ProjectContextManager,
    build_project_context,
    validate_and_build_project_context,
)
from context.workspace import (
    GitInfo,
    WorkspaceInfo,
    WorkspaceValidator,
    validate_workspace,
    is_workspace_ambiguous,
)

__all__ = [
    'ContextManager',
    'MessageItem',
    'GitInfo',
    'WorkspaceInfo',
    'WorkspaceValidator',
    'validate_workspace',
    'is_workspace_ambiguous',
    'ProjectContext',
    'ProjectContextManager',
    'build_project_context',
    'validate_and_build_project_context',
]