import json
import uuid
from typing import Callable

from agent.store import (
    Checkpoint,
    SessionMeta,
    SessionRecord,
    SessionStore,
    UNTITLED,
    make_title,
)
from utils.development_supervisor import DevelopmentSupervisor
from client.llm_client import LLMClient
from client.response import TokenUsage
from config.config import Config
from config.loader import get_data_dir
from context.compaction import ChatCompactor
from context.loop_detector import LoopDetector
from context.manager import ContextManager
from context.project_context import (
    build_project_context,
    validate_and_build_project_context,
)
from context.workspace import validate_workspace
from hooks.hook_system import HookSystem
from safety.approval import ApprovalManager
from tools.discovery import ToolDiscoveryManager
from tools.mcp.manager import MCPManager
from tools.registry import create_default_registry
from datetime import datetime

class Session:
    def __init__(self, config: Config):
        self.config = config
        self.development_supervisor = DevelopmentSupervisor(config.cwd)
        self.client = LLMClient(
            config=config,
        )
        self.context_manager: ContextManager | None = None
        self.tool_registry = create_default_registry(config)
        self.discovery_manager = ToolDiscoveryManager(
            self.config,
            self.tool_registry
        )
        self.mcp_manager = MCPManager(
            self.config
        )
        self.chat_compactor = ChatCompactor(self.client)
        self.approval_manager = ApprovalManager(
            self.config.approval, 
            self.config.cwd, 
        )
        self.loop_detector = LoopDetector()
        self.hook_system = HookSystem(config)
        self.session_id = str(uuid.uuid4()) # Unique identifiers to resume sessions
        self.workspace = validate_workspace(config.cwd)
        self.project_context = None  # set during initalize()
        self.project_context_hint: str | None = None  # ambiguity hint, if any
        self.created_at = datetime.now()
        self.updated_at = datetime.now()

        self.store = SessionStore()
        self.title = UNTITLED
        self.resumed_from: SessionRecord | None = None

        self.last_usage: TokenUsage | None = None

        self.turn_usage = TokenUsage()

        self._turn_count = 0

        # What the last checkpoint held, so exiting does not write a duplicate.
        self._saved_signature: tuple[int, int] | None = None

    async def initalize(self, progress: Callable[[str], None] | None = None) -> None:

        def _step(label: str) -> None:
            if progress:
                progress(label)

        _step("initializing workspace")
        await self.mcp_manager.initialize()
        _step("registering MCP tools")
        self.mcp_manager.register_tools(self.tool_registry)
        _step("discovering tools")
        self.discovery_manager.discover()
        _step("building context manager")
        self.context_manager = ContextManager(
            config=self.config,
            user_memory=self._load_memory(),
            tools=self.tool_registry.get_tools(),
        )
        _step("validating project context")
        self.project_context, self.project_context_hint = (
            validate_and_build_project_context(self.config, self.config.cwd)
        )
    
    def _load_memory(self) -> str | None:

        data_dir = get_data_dir()

        data_dir.mkdir(
            parents=True, 
            exist_ok=True
        )

        path = data_dir / 'user_memory.json'

        lines: list[str] = []
        try:
            if path.exists():
                content = path.read_text(encoding="utf-8")
                entries = json.loads(content).get("entries") or {}
                if entries:
                    lines.append("User preferences and notes:")
                    lines.extend(f"- {key}: {value}" for key, value in entries.items())
        except Exception:
            pass
        resume = self.development_supervisor.resume_summary()
        if resume:
            lines.append(resume)
        return "\n\n".join(lines) or None

    @property
    def short_id(self) -> str:
        return self.session_id[:8]

    def note_prompt(self, message: str) -> None:
        """The first prompt of a session becomes its title in the session list."""

        if self.title == UNTITLED and message.strip():
            self.title = make_title(message)

    def meta(self) -> SessionMeta:
        usage = (
            self.context_manager.total_usage
            if self.context_manager
            else TokenUsage()
        )
        return SessionMeta(
            session_id=self.session_id,
            cwd=str(self.config.cwd.resolve()),
            model=self.config.model_name,
            title=self.title,
            created_at=self.created_at,
            updated_at=self.updated_at,
            turns=self._turn_count,
            usage={
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "cached_tokens": usage.cached_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
            },
        )

    def save_checkpoint(
        self,
        label: str | None = None,
        auto: bool = True,
    ) -> Checkpoint | None:
        """Write the conversation to disk. Never raises: losing a checkpoint
        should not take the turn down with it."""

        self.development_supervisor.checkpoint(
            current_project=self.config.cwd.name,
            project_type=self.project_context.project_type if self.project_context else "unknown",
            workspace_root=str(self.config.cwd.resolve()),
        )

        if not self.config.session.enabled or self.context_manager is None:
            return None

        messages = self.context_manager.export_messages()
        if not messages:
            return None

        signature = (len(messages), hash(messages[-1].get("content") or ""))
        if auto and signature == self._saved_signature:
            return None

        self.updated_at = datetime.now()

        try:
            new_session = not self.store.exists(self.session_id)
            checkpoint = self.store.save(
                self.meta(),
                messages,
                label=label,
                auto=auto,
                max_checkpoints=self.config.session.max_checkpoints,
            )
            if new_session:
                self.store.prune(self.config.session.max_sessions)
        except (OSError, TypeError, ValueError):
            return None

        self._saved_signature = signature
        return checkpoint

    def checkpoints(self) -> list[Checkpoint]:
        meta = self.store.read_meta(self.session_id)
        return meta.checkpoints if meta else []

    def restore(self, record: SessionRecord) -> int:
        """Adopt a saved transcript, identity and all."""

        if self.context_manager is None:
            raise RuntimeError("Session must be initialised before restoring.")

        self.session_id = record.meta.session_id
        self.title = record.meta.title
        self.created_at = record.meta.created_at
        self.updated_at = record.meta.updated_at
        self._turn_count = record.checkpoint.turn

        restored = self.context_manager.restore_messages(record.messages)
        self.context_manager.set_total_usage(
            TokenUsage(
                prompt_tokens=record.meta.usage.get("prompt_tokens", 0),
                completion_tokens=record.meta.usage.get("completion_tokens", 0),
                total_tokens=record.meta.usage.get("total_tokens", 0),
                cached_tokens=record.meta.usage.get("cached_tokens", 0),
                reasoning_tokens=record.meta.usage.get("reasoning_tokens", 0),
            )
        )

        # A restored transcript is by definition already on disk.
        messages = self.context_manager.export_messages()
        self._saved_signature = (
            (len(messages), hash(messages[-1].get("content") or ""))
            if messages
            else None
        )

        self.loop_detector = LoopDetector()
        self.last_usage = None
        self.turn_usage = TokenUsage()
        self.resumed_from = record

        return restored

    def reset(self) -> str:
        """Start over under a new id, leaving whatever was saved on disk."""

        self.session_id = str(uuid.uuid4())
        self.created_at = datetime.now()
        self.updated_at = self.created_at
        self.title = UNTITLED
        self.resumed_from = None
        self._turn_count = 0
        self._saved_signature = None
        self.last_usage = None
        self.turn_usage = TokenUsage()
        self.loop_detector = LoopDetector()

        if self.context_manager is not None:
            self.context_manager.clear()
            self.context_manager.set_total_usage(TokenUsage())

        return self.session_id

    def resume(
        self,
        session_id: str,
        checkpoint_id: str | None = None,
    ) -> SessionRecord | None:
        """Load a session by id or prefix and continue it. None if unknown."""

        resolved = self.store.resolve(session_id, self.config.cwd)
        if resolved is None:
            return None

        record = self.store.load(resolved, checkpoint_id)
        if record is None:
            return None

        self.restore(record)
        return record

    def reset_turn_usage(self) -> None:
        self.turn_usage = TokenUsage()
    
    def inc_turn(self) -> int:
        """Count one user turn. A turn may span many model round-trips."""
        self._turn_count += 1
        self.updated_at = datetime.now()

        return self._turn_count

    @property
    def turns(self) -> int:
        return self._turn_count
